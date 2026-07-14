"""Unit tests for the stroke_classification stage.

No weight file, no GPU: BST is either built with random weights or replaced outright by a
canned probability table, because nothing here is testing the network. The stage is a thin
shell around it, and the shell has exactly three seams that can break.

**The windows.** This stage is a port of ``bst_infer_standalone.py``, which used to sit in
the repo root and read four ad-hoc CSVs. Everything it did already lived in
``modules.common.bst`` — the model, the normalization, the label space, the artifact
adapter — except one function: ``build_segments``, the ``between_2_hits_with_max_limits``
scheme the checkpoint was trained under. That is the only logic that actually moved, so
``test_windows_match_the_reference_implementation`` pins the port against a frozen copy of
it. This is the failure mode with no symptom: a window off by a few frames does not crash,
it just quietly feeds the model something it was not trained on.

**The alignment.** ``strokes.json`` is one record per ``HitEvent``, in the same order. An
off-by-one here mislabels every hit in the match while looking perfectly well-formed.

**The honesty of 未知球種.** Class 0 is a real answer and has to survive to the artifact as
one, rather than being rounded up to the best of the 24 real strokes.
"""

from __future__ import annotations

import csv

import numpy as np
import pytest

from modules.artifacts import read_artifact, write_artifact
from modules.base import StageStatus, read_status
from modules.common.bst import SegmentFeatures, between_hits_windows, build_bst_model
from modules.common.bst.classes import (
    L_ANKLE,
    N_CLASSES,
    NUM_KEYPOINTS,
    R_ANKLE,
    STROKE_CLASSES,
    UNKNOWN_INDEX,
)
from modules.contracts import PIPELINE, pipeline_order, stage_path
from modules.stroke_classification import predict
from modules.stroke_classification.config import StrokeClassificationConfig
from modules.stroke_classification.module import StrokeClassificationModule
from modules.stroke_classification.predict import label_of

FPS = 30.0
# At 30 fps: the ±0.5 s fallback reach, the ±1.5 s max limit, the 0.25 s tail.
LEAD, LIMIT, EPS = 15, 45, 7

SMASH_TOP = STROKE_CLASSES.index("Top_殺球")
LIFT_BOTTOM = STROKE_CLASSES.index("Bottom_挑球")
SERVE_BOTTOM = STROKE_CLASSES.index("Bottom_發短球")


# --------------------------------------------------------------------------- #
# The windows — between_2_hits_with_max_limits
# --------------------------------------------------------------------------- #


def reference_build_segments(hits, fps, n_frames):
    """``bst_infer_standalone.build_segments``, frozen verbatim.

    The reference is gone from the repo; this is what it did, kept only so the port can be
    held against it. Do not tidy it — its value is being character-for-character what the
    checkpoint was trained with.
    """
    t = int(fps // 2)
    limit = int(fps * 3 // 2)
    eps = t // 2
    frames = sorted(hits)
    segments = []
    for idx, hit in enumerate(frames):
        has_prev = idx > 0
        has_next = idx < len(frames) - 1
        start_f = frames[idx - 1] if has_prev else (hit - t)
        end_f = (frames[idx + 1] + eps) if has_next else (hit + t)
        if start_f < hit - limit:
            start_f = hit - limit
        if end_f > hit + limit + eps:
            end_f = hit + limit + eps
        start_f = max(0, int(start_f))
        end_f = min(n_frames, int(end_f) + 1)
        segments.append((start_f, end_f))
    return segments


@pytest.mark.parametrize("fps", [25.0, 30.0, 59.94])
def test_windows_match_the_reference_implementation(fps):
    """The whole point of the port: same hits in, same windows out. Fuzzed."""
    rng = np.random.default_rng(20260714)
    for _ in range(300):
        n_frames = int(rng.integers(60, 900))
        n_hits = int(rng.integers(1, 13))
        hits = sorted(rng.choice(n_frames, size=min(n_hits, n_frames), replace=False).tolist())

        assert between_hits_windows(hits, n_frames, fps) == reference_build_segments(
            hits, fps, n_frames
        )


def test_a_lone_hit_falls_back_to_half_a_second_either_side():
    """No previous hit and no next one, so there is nothing to run *between*."""
    assert between_hits_windows([100], n_frames=300, fps=FPS) == [
        (100 - LEAD, 100 + LEAD + 1)
    ]


def test_a_window_runs_from_the_previous_hit_to_just_past_the_next_one():
    """A stroke is legible from the reply leaving, not from the contact alone — hence eps."""
    windows = between_hits_windows([100, 140, 170], n_frames=300, fps=FPS)

    # The middle hit is the only one with a neighbour on both sides.
    assert windows[1] == (100, 170 + EPS + 1)
    # The first reaches back half a second (no previous hit) and forward past the second.
    assert windows[0] == (100 - LEAD, 140 + EPS + 1)
    # The last reaches back to the previous hit and forward half a second.
    assert windows[2] == (140, 170 + LEAD + 1)


def test_a_long_lull_cannot_drag_the_window_past_the_max_limit():
    """The rule the checkpoint's filename is named after. 250 frames apart at 30 fps is
    over 8 seconds — without the limit, one window would swallow most of the rally."""
    windows = between_hits_windows([50, 300], n_frames=400, fps=FPS)

    assert windows[0] == (50 - LEAD, 50 + LIMIT + EPS + 1)   # forward reach clamped
    assert windows[1] == (300 - LIMIT, 300 + LEAD + 1)       # backward reach clamped


def test_windows_are_clipped_to_the_rally_and_always_contain_their_hit():
    windows = between_hits_windows([0, 8, 29], n_frames=30, fps=FPS)

    assert windows[0][0] == 0                     # not negative
    assert windows[-1][1] == 30                   # not past the end
    for (start, end), hit in zip(windows, [0, 8, 29]):
        assert start <= hit < end                 # a window without its own hit is useless


def test_a_hit_outside_the_rally_is_an_error_rather_than_an_empty_window():
    with pytest.raises(ValueError, match="outside the segment"):
        between_hits_windows([10, 500], n_frames=100, fps=FPS)


def test_no_hits_is_no_windows():
    assert between_hits_windows([], n_frames=100, fps=FPS) == []


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #


def probabilities_for(winner: int, confidence: float = 0.9) -> np.ndarray:
    row = np.full(N_CLASSES, (1.0 - confidence) / (N_CLASSES - 1), dtype=np.float32)
    row[winner] = confidence
    return row


def test_the_winning_class_carries_both_the_stroke_and_the_hitter():
    """One forward pass answers "which stroke" and "who hit it" — that is the 25-class head."""
    label = label_of(
        probabilities_for(SMASH_TOP, 0.8), event_index=3, frame=250, segment_index=1
    )

    assert (label.event_index, label.frame, label.segment_index) == (3, 250, 1)
    assert label.player == "top"
    assert label.stroke_type == "殺球"
    assert label.confidence == pytest.approx(0.8)


def test_the_stroke_is_reported_in_the_eight_classes_users_see():
    # 挑球 is one of the two strokes BST cannot separate from 長球, so both report 高遠球.
    label = label_of(probabilities_for(LIFT_BOTTOM), event_index=0, frame=0, segment_index=0)
    assert label.stroke_type == "高遠球"
    assert label.player == "bottom"


def test_a_hit_the_model_cannot_read_stays_unknown_rather_than_becoming_a_guess():
    """未知球種 is an answer, not a gap.

    The runner-up here is a confident-looking 殺球. Promoting it would hand the pipeline a
    specific claim about a hit the model just said it could not read — and nothing
    downstream could tell the difference.
    """
    row = np.full(N_CLASSES, 0.001, dtype=np.float32)
    row[UNKNOWN_INDEX] = 0.6
    row[SMASH_TOP] = 0.35

    label = label_of(row, event_index=0, frame=0, segment_index=0)

    assert label.stroke_type == "未知球種"
    assert label.player is None
    assert label.confidence == pytest.approx(0.6)


# --------------------------------------------------------------------------- #
# The stage
# --------------------------------------------------------------------------- #

SCALE, ORIGIN_X, ORIGIN_Y = 100.0, 200.0, 100.0
COURT_TO_IMAGE = [[SCALE, 0.0, ORIGIN_X], [0.0, SCALE, ORIGIN_Y], [0.0, 0.0, 1.0]]
COURT_WIDTH_M, COURT_LENGTH_M = 6.10, 13.41
VIDEO_SIZE = (1920, 1080)

START_FRAME, END_FRAME = 100, 299                  # one 200-frame rally
HIT_FRAMES = [140, 190, 240, 280]                  # absolute
LOCAL_HITS = [f - START_FRAME for f in HIT_FRAMES]


def skeleton_at(x_m: float, y_m: float) -> tuple[list, list]:
    foot_x, foot_y = ORIGIN_X + SCALE * x_m, ORIGIN_Y + SCALE * y_m
    keypoints = [[foot_x, foot_y - 170.0, 0.9] for _ in range(NUM_KEYPOINTS)]
    keypoints[L_ANKLE] = [foot_x - 10.0, foot_y, 0.9]
    keypoints[R_ANKLE] = [foot_x + 10.0, foot_y, 0.9]
    return keypoints, [foot_x - 30.0, foot_y - 180.0, foot_x + 30.0, foot_y]


@pytest.fixture
def match(tmp_path, monkeypatch):
    """One rally, both players present in every frame, four hits.

    The shuttle is normalized against the *video's* resolution, which the adapter probes
    from the match video — deliberately, since it has to be the resolution the upstream
    stages measured in, and a config knob for it would be a knob for getting it wrong. So
    the probe is stubbed here rather than the stage being made to take the size.
    """
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "match.mp4").write_bytes(b"")      # never decoded
    monkeypatch.setattr("modules.common.bst.adapter.video_size", lambda _: VIDEO_SIZE)

    frames = range(START_FRAME, END_FRAME + 1)
    pose_records = []
    for frame in frames:
        for player, y_m in (("top", 1.0), ("bottom", COURT_LENGTH_M - 1.0)):
            keypoints, bbox = skeleton_at(COURT_WIDTH_M / 2, y_m)
            pose_records.append({"frame": frame, "segment_index": 0, "player": player,
                                 "keypoints": keypoints, "bbox": bbox})
    shuttle_records = [
        {"frame": frame, "segment_index": 0, "method": "inpaint",
         "x": 960.0, "y": 300.0 + 200.0 * np.sin(frame / 10.0), "visible": True,
         "confidence": 0.9}
        for frame in frames
    ]

    artifacts = {
        "match_segmentation": (
            [{"start_frame": START_FRAME, "end_frame": END_FRAME, "start_sec": 0.0,
              "end_sec": 6.6, "duration_sec": 6.6}],
            {"fps": FPS},
        ),
        "court_detection": (
            [{"corners": [[0, 0]] * 4, "homography": COURT_TO_IMAGE, "segment_index": None}],
            None,
        ),
        "pose": (pose_records, None),
        "shuttle_tracking": (shuttle_records, None),
        "event_detection": ([{"frame": f} for f in HIT_FRAMES], {"fps": FPS}),
    }
    for stage, (records, extra) in artifacts.items():
        spec = PIPELINE[stage]
        write_artifact(spec, records, stage_path(tmp_path, stage) / spec.output_filename, extra)
    return tmp_path


@pytest.fixture
def canned_bst(monkeypatch):
    """Replace BST with a fixed probability table, one row per window, in window order.

    The network itself is tested in ``test_bst.py``; what is under test here is the
    plumbing around it, and a random model would let a misalignment pass by producing
    an equally arbitrary answer either way.
    """
    rows = [
        probabilities_for(SERVE_BOTTOM, 0.7),      # hit 0 — the serve
        probabilities_for(SMASH_TOP, 0.8),         # hit 1
        probabilities_for(LIFT_BOTTOM, 0.6),       # hit 2
        np.full(N_CLASSES, 0.004, dtype=np.float32),   # hit 3 — the model has no idea
    ]
    rows[3][UNKNOWN_INDEX] = 0.9

    monkeypatch.setattr(
        "modules.stroke_classification.module.load_bst_model", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        "modules.stroke_classification.predict.predict_windows",
        lambda model, features, windows, **kwargs: np.stack(rows[:len(windows)]),
    )
    return rows


def run_stage(match_path, **config_kwargs):
    module = StrokeClassificationModule(
        config=StrokeClassificationConfig(device="cpu", **config_kwargs)
    )
    return module.run(match_path)


def test_every_hit_gets_a_stroke_and_a_hitter(match, canned_bst):
    output = run_stage(match)

    envelope = read_artifact(PIPELINE["stroke_classification"], output)
    strokes = envelope["strokes"]

    assert [s["stroke_type"] for s in strokes] == ["發球", "殺球", "高遠球", "未知球種"]
    assert [s["player"] for s in strokes] == ["bottom", "top", "bottom", None]
    assert envelope["shuttle_method"] == "inpaint"
    assert read_status(stage_path(match, "stroke_classification")).status == StageStatus.COMPLETED


def test_records_line_up_with_events_json_one_for_one(match, canned_bst):
    """``event_index`` is a position in ``events.json``. Off by one and every hit in the
    match is mislabelled, in an artifact that still validates."""
    output = run_stage(match)
    strokes = read_artifact(PIPELINE["stroke_classification"], output)["strokes"]

    assert len(strokes) == len(HIT_FRAMES)
    assert [s["event_index"] for s in strokes] == list(range(len(HIT_FRAMES)))
    assert [s["frame"] for s in strokes] == HIT_FRAMES        # absolute, as HitEvent had it
    assert {s["segment_index"] for s in strokes} == {0}


def test_hits_are_classified_through_the_windows_the_checkpoint_was_trained_on(match, monkeypatch):
    """The windows the stage actually hands the model are the between-hits ones — not the
    ±0.5 s centred windows event_detection's dense scan uses."""
    seen: list = []

    def capture(model, features, windows, **kwargs):
        seen.extend(windows)
        return np.stack([probabilities_for(SMASH_TOP) for _ in windows])

    monkeypatch.setattr(
        "modules.stroke_classification.module.load_bst_model", lambda *a, **k: object()
    )
    monkeypatch.setattr("modules.stroke_classification.predict.predict_windows", capture)
    run_stage(match)

    assert seen == between_hits_windows(LOCAL_HITS, END_FRAME - START_FRAME + 1, FPS)


def test_a_hit_in_no_rally_means_the_artifacts_disagree_and_the_stage_says_so(match, canned_bst):
    """events.json older than segments.json. Classifying it anyway would produce records
    aligned to neither cut of the match."""
    spec = PIPELINE["event_detection"]
    write_artifact(
        spec,
        [{"frame": f} for f in [*HIT_FRAMES, 9999]],
        stage_path(match, "event_detection") / spec.output_filename,
        {"fps": FPS},
    )

    with pytest.raises(RuntimeError, match="falls in no rally segment"):
        run_stage(match)
    assert read_status(stage_path(match, "stroke_classification")).status == StageStatus.FAILED


def test_the_debug_csv_explains_each_hit(match, canned_bst, tmp_path):
    csv_path = tmp_path / "strokes.csv"
    module = StrokeClassificationModule(config=StrokeClassificationConfig(device="cpu"))
    module.run(match, debug_csv=csv_path)

    with csv_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == len(HIT_FRAMES)
    assert [int(r["Frame"]) for r in rows] == HIT_FRAMES
    # The un-merged 25-class name is kept, so a 高遠球 can still be traced back to 挑球.
    assert rows[2]["RawClass"] == "Bottom_挑球"
    assert rows[2]["Stroke"] == "高遠球"
    # Side evidence, straight out of this hit's own row — the numbers event_detection fuses.
    assert float(rows[1]["p_top"]) > float(rows[1]["p_bottom"])
    assert float(rows[3]["p_unknown"]) > 0.8
    assert rows[0]["Top1"].startswith("Bottom_發短球")


def test_the_real_network_runs_end_to_end_on_these_windows(match, monkeypatch):
    """No canned table: BST (random weights) is actually fed the windows this stage cuts.

    Nothing is asserted about *which* stroke comes out — the weights are random. What is
    asserted is that the tensors this stage builds are ones the network accepts.
    """
    monkeypatch.setattr(
        "modules.stroke_classification.module.load_bst_model",
        lambda *a, **k: build_bst_model().eval(),
    )
    output = run_stage(match)

    strokes = read_artifact(PIPELINE["stroke_classification"], output)["strokes"]
    assert len(strokes) == len(HIT_FRAMES)
    assert all(0.0 <= s["confidence"] <= 1.0 for s in strokes)
    assert all(s["stroke_type"] for s in strokes)


def test_classify_segment_keeps_hits_and_windows_paired_when_events_arrive_out_of_order(
    monkeypatch,
):
    """``events.json`` is written sorted, but nothing in the contract *promises* that, and
    pairing hit 3's window with hit 1's event index would be silent."""
    features = SegmentFeatures(
        joints=np.zeros((200, 2, NUM_KEYPOINTS, 2), np.float32),
        positions=np.zeros((200, 2, 2), np.float32),
        shuttle=np.zeros((200, 2), np.float32),
        start_frame=100,
    )
    monkeypatch.setattr(
        "modules.stroke_classification.predict.predict_windows",
        lambda model, features, windows, **kwargs: np.stack(
            [probabilities_for(SMASH_TOP) for _ in windows]
        ),
    )

    # Event 0 is the *later* hit; event 1 the earlier one.
    predictions = predict.classify_segment(
        None, features, [(0, 150), (1, 40)], FPS, segment_index=0
    )

    assert [(p.label.event_index, p.local_frame) for p in predictions] == [(1, 40), (0, 150)]
    for prediction in predictions:
        start, end = prediction.window
        assert start <= prediction.local_frame < end
        assert prediction.label.frame == 100 + prediction.local_frame


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def test_the_stage_is_registered_and_runs_after_everything_it_reads():
    from modules.runner import available_modules

    modules = available_modules()
    assert "stroke_classification" in modules

    order = pipeline_order()
    for dependency in PIPELINE["stroke_classification"].dependencies:
        assert order.index(dependency) < order.index("stroke_classification")
