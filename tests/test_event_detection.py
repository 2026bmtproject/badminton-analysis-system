"""Unit tests for the event_detection stage.

No weight file, no GPU: every test either works on hand-built geometry or seeds the
dense-scan cache directly, which is the property that makes phase 2 cheap to iterate on in
the first place.

This stage is a port of ``detect_events_v632/``, which used to sit in the repo root. While
it was there, a migration test pinned the two against each other on identical inputs —
same trajectory, same dense scan, same frames out, down to which gate rejected which
candidate. It passed, the reference was deleted, and the test went with it.

What is left here is what still has to be true without it: the seams that are new (the
artifacts -> trajectory / skeleton adapters, the cache, the offsets, and the scoreboard
rule's absence being *loud*), and the detector's behaviour stated in its own terms — gaps
get filled, tails get pruned, and every prune rule fails open.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from modules.artifacts import read_artifact, write_artifact
from modules.base import StageStatus, StageState, write_status
from modules.common.bst import adapter
from modules.common.bst.classes import N_CLASSES, STROKE_CLASSES, UNKNOWN_INDEX
from modules.contracts import COCO_KEYPOINTS, PIPELINE, pipeline_order, stage_path
from modules.event_detection import dense_cache
from modules.event_detection.complete import complete_segment
from modules.event_detection.config import EventDetectionConfig
from modules.event_detection.evidence import Dense
from modules.event_detection.module import EventDetectionModule, scan_windows
from modules.event_detection.prune import dead_segments, prune_segment, rally_span
from modules.event_detection.sides import SideOf, skeletons_by_segment
from modules.event_detection.streams import run_stream
from modules.event_detection.trajectory import Traj, build_trajectories

SMASH_TOP = STROKE_CLASSES.index("Top_殺球")
SMASH_BOTTOM = STROKE_CLASSES.index("Bottom_殺球")
SERVE_BOTTOM = STROKE_CLASSES.index("Bottom_發短球")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def probabilities_with(n_frames: int, blocks: list[tuple[int, int, int]]) -> np.ndarray:
    """``(n, 25)`` probabilities: 未知球種 everywhere, except ``(start, end, class)`` blocks."""
    probabilities = np.full((n_frames, N_CLASSES), 0.01, dtype=np.float32)
    probabilities[:, UNKNOWN_INDEX] = 0.9
    for start, end, cls in blocks:
        probabilities[start:end, :] = 0.005
        probabilities[start:end, cls] = 0.9
    return probabilities / probabilities.sum(axis=1, keepdims=True)


def arc_trajectory(n_frames: int = 240, period: int = 40) -> Traj:
    """A rally of clean alternating arcs, every frame tracked. Hits are at the arc tops."""
    frames = list(range(n_frames))
    xs, ys = [], []
    for f in frames:
        phase = (f % period) / period
        arc = f // period
        direction = 1 if arc % 2 == 0 else -1
        xs.append(960 + direction * (phase - 0.5) * 700)
        ys.append(640 - 300 * np.sin(np.pi * phase))
    return Traj(frames, [True] * n_frames, xs, ys)


# --------------------------------------------------------------------------- #
# trajectory
# --------------------------------------------------------------------------- #


def test_traj_reads_visibility_not_a_zero_sentinel():
    """An invisible frame has no coordinates — and a *real* point at x=0 is still a point.

    The reference keyed off ``x == 0``, so a shuttle tracked to the very left edge of the
    frame would have vanished from its trajectory. ``shuttle.json`` says outright whether
    the frame was seen, and this reads that instead.
    """
    records = [
        {"frame": 10, "segment_index": 0, "method": "inpaint", "x": 0.0, "y": 500.0, "visible": True},
        {"frame": 11, "segment_index": 0, "method": "inpaint", "x": None, "y": None, "visible": False},
        {"frame": 12, "segment_index": 0, "method": "inpaint", "x": 800.0, "y": 400.0, "visible": True},
        {"frame": 10, "segment_index": 0, "method": "viterbi", "x": 5.0, "y": 5.0, "visible": True},
    ]
    trajectories = build_trajectories(records, "inpaint")
    traj = trajectories[0]

    assert traj.frames == [10, 11, 12]        # frames stay dense
    assert traj.at == {10: (0.0, 500.0), 12: (800.0, 400.0)}   # x=0 survives
    assert 11 not in traj.at                                    # the invisible one does not
    assert list(traj.vf) == [10, 12]
    assert build_trajectories(records, "viterbi")[0].at == {10: (5.0, 5.0)}


def test_amp_declines_rather_than_reporting_zero():
    """Too few tracked points -> None ("cannot say"), which every gate treats as a pass."""
    frames = list(range(40))
    visible = [i in (0, 1, 2) for i in frames]
    traj = Traj(frames, visible, [500.0] * 40, [500.0] * 40)
    assert traj.amp(20, win=18) is None
    assert traj.amp_pass(20, yamp_min=100, xamp_min=40, win=18) is True


def test_y_peaks_find_the_arc_tops():
    """Image y is inverted, so the shuttle at its *lowest* is a peak in the array.

    The arcs in the fixture meet at frames 40, 80, ... — which is where a racket met the
    shuttle, and exactly the frames a hit detector has to fire on.
    """
    traj = arc_trajectory(n_frames=240, period=40)
    assert sorted(traj.y_peaks(prom=20)) == [40, 80, 120, 160, 200]


def test_edge_peak_trim_measures_a_depth_not_an_index():
    """The end peaks are judged against the *valley beside them*, not against its index.

    Every version of this before the port compared the peak's height to ``argmin``'s return
    — a frame offset — which made the test unfalsifiable for any real arc (a ~540 px peak
    is never within 5 of a ~12 frame offset) and the trim dead code. The two readings are
    pinned apart here: a first peak sitting 2 px above its valley must go, and it only goes
    if the depth is what is being measured.
    """
    from modules.event_detection.signals import _trim_edge_peaks

    # Five peaks. The first rises 2 px out of its valley; the rest rise 100.
    y = np.array([
        302.0, 300.0,                       # peak 0 at index 0, valley 2 px below
        400.0, 300.0, 400.0, 300.0, 400.0,  # peaks at 2, 4, 6
        300.0, 400.0,                       # peak at 8
    ])
    peaks = np.array([0, 2, 4, 6, 8])

    kept = _trim_edge_peaks(peaks, y, edge_prom=5)
    assert 0 not in kept, "a peak 2 px above its valley is noise and must be dropped"

    # The index reading would have computed y[0] - argmin(y[0:2]) = 302 - 1 = 301, which is
    # not < 5, and kept it. Guard against that arithmetic coming back.
    assert (y[peaks[0]] - np.argmin(y[peaks[0]:peaks[1]])) > 5


def test_first_rise_finds_the_serve():
    """A sustained climb = the serve leaving the racket; the answer is where it starts.

    The shuttle falls for 10 frames (image y grows) and then is hit upward. Frame 9 is the
    bottom of that fall — the last frame before it climbs, and the frame the racket met it.
    """
    frames = list(range(60))
    falling = [600.0 + 10 * i for i in range(10)]              # dropped: y grows to 690
    climbing = [690.0 - 12 * i for i in range(1, 30)]          # struck: y shrinks
    ys = falling + climbing + [350.0] * 21
    traj = Traj(frames, [True] * 60, [900.0] * 60, ys)
    assert traj.first_rise(min_len=6, min_rise=60) == 9


# --------------------------------------------------------------------------- #
# dense scan
# --------------------------------------------------------------------------- #


def test_scan_windows_are_centered_on_their_frame_and_clipped_at_the_rally_edges():
    """The scan asks "is frame f a hit?", so a window that is not centred on f is asking
    about a different frame. At the rally's edges it is shortened, never shifted."""
    windows = scan_windows(n_frames=10, half=3)

    assert len(windows) == 10
    assert windows[0] == (0, 4)          # clipped at the start, not shifted right
    assert windows[5] == (2, 9)          # a full window in the middle
    assert windows[9] == (6, 10)         # clipped at the end


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #


def test_lock_regions_need_length_and_confidence():
    probabilities = probabilities_with(120, [(20, 32, SMASH_TOP), (60, 63, SMASH_BOTTOM)])
    dense = Dense(probabilities, start_frame=1000)

    regions = dense.lock_regions(min_run=7, conf_min=0.65)
    assert [(r.f0, r.f1) for r in regions] == [(1020, 1031)]   # the 3-frame one is too short
    assert regions[0].labels[0] == "Top_殺球"

    # start_frame is carried: these are absolute frames, like every other stage's
    assert dense.onsets(conf_min=0.5, min_len=3) == [1020, 1060]


def test_side_map_leaves_the_undecidable_out():
    """A frame neither side leads on is *absent* from the map, not guessed.

    Deep in the 未知球種 stretch between two hits the two sides carry equal mass, and equal
    mass clears a 1.2x margin in neither direction. ``sides.SideOf`` falls back to the
    skeletons on exactly those frames, which it can only do if they are missing rather than
    filled in with a coin flip.
    """
    probabilities = probabilities_with(60, [(10, 20, SMASH_TOP), (40, 50, SMASH_BOTTOM)])
    side_map = Dense(probabilities, start_frame=0).side_map(win=3, margin=1.2)

    assert side_map[15] == "top"
    assert side_map[45] == "bottom"
    assert 30 not in side_map


def test_has_serve_reads_the_class_name():
    assert Dense(probabilities_with(60, [(10, 20, SERVE_BOTTOM)]), 0).has_serve()
    assert not Dense(probabilities_with(60, [(10, 20, SMASH_TOP)]), 0).has_serve()


def test_empty_dense_is_falsy_and_answers_safely():
    dense = Dense(None, start_frame=0)
    assert not dense
    assert dense.onsets() == []
    assert dense.side_map() == {}
    assert dense.conf_near(5) == 0.0
    assert not dense.has_serve()


# --------------------------------------------------------------------------- #
# sides
# --------------------------------------------------------------------------- #


def _arm(shoulder, elbow, wrist):
    """A minimal usable skeleton: a scored shoulder/elbow/wrist chain gives an arm length."""
    joints = {name: (0.0, 0.0, 0.0) for name in COCO_KEYPOINTS}
    joints["L_shoulder"] = (*shoulder, 0.9)
    joints["L_elbow"] = (*elbow, 0.9)
    joints["L_wrist"] = (*wrist, 0.9)
    return joints


def test_bst_side_wins_and_snaps_to_a_neighbour():
    side_of = SideOf({100: "top"}, None, ball_at={}, snap=4)
    assert side_of(100) == "top"
    assert side_of(103) == "top"     # within snap
    assert side_of(120) is None      # beyond it, with no skeletons to fall back on


def test_skeleton_fallback_normalizes_by_arm_length():
    """Distances are measured in arm-lengths, and that changes the answer.

    Perspective compresses the far half of the court, so the far player is *small* — and
    raw pixel distances flatter them: here the shuttle is 90 px from top's wrist and 120 px
    from bottom's, so counting pixels says top. But 90 px is nine tenths of top's whole
    100 px arm (they are nowhere near it) while 120 px is under half of bottom's 300 px arm
    (they are on it). In the unit that means anything — the player's own scale — bottom hit
    it, and that is who this returns.
    """
    ball = (900.0, 500.0)
    skeletons = {
        7: {
            # far court, small in frame: arm 100 px, wrist 90 px from the shuttle -> 0.90
            "top": _arm((710.0, 500.0), (760.0, 500.0), (810.0, 500.0)),
            # near court, large in frame: arm 300 px, wrist 120 px away -> 0.40
            "bottom": _arm((1320.0, 500.0), (1170.0, 500.0), (1020.0, 500.0)),
        }
    }
    side_of = SideOf({}, skeletons, ball_at={7: ball}, margin=1.3)
    assert side_of(7) == "bottom"


def test_skeletons_by_segment_drops_frames_with_no_player():
    records = [
        {"frame": 5, "segment_index": 0, "player": "top",
         "keypoints": [[1.0, 2.0, 0.9]] * 17, "bbox": [0, 0, 10, 10]},
        {"frame": 5, "segment_index": 0, "player": "bottom", "keypoints": None, "bbox": None},
    ]
    out = skeletons_by_segment(records, COCO_KEYPOINTS)
    assert set(out[0][5]) == {"top"}                     # bottom was not found in frame 5
    assert out[0][5]["top"]["nose"] == (1.0, 2.0, 0.9)   # aligned to COCO_KEYPOINTS


# --------------------------------------------------------------------------- #
# complete / prune — the rules that must still hold after the reference is gone
# --------------------------------------------------------------------------- #


def test_alternation_gap_is_filled_from_the_dense_scan():
    """Two same-side hits in a row means the opponent hit in between and was missed.

    With no trajectory signal and no aux stream to draw from, the fill comes from BST
    alone — the weighted centre of an opposite-side run, tagged ``dense`` so the output
    offset knows it is not a turning-point estimate.
    """
    config = EventDetectionConfig()
    traj = arc_trajectory(n_frames=240, period=40)

    # Say both real hits were called "top", and BST saw bottom hitting in between.
    side_map = {f: "top" for f in range(240)}
    side_map.update({f: "bottom" for f in range(70, 90)})
    probabilities = probabilities_with(240, [(70, 90, SMASH_BOTTOM)])
    dense = Dense(probabilities, start_frame=0)

    side_of = SideOf(side_map, None, traj.at, snap=config.side.bst_snap)
    base = run_stream(traj, side_of, config.signal, config.select)
    base.kept = [40, 120]                       # a same-side pair with a hole between them

    hits, additions = complete_segment(
        base, None, dense, config.signal, config.select, config.complete
    )
    filled = [(f, source, tag) for f, source, tag in additions if tag == "alt_fill"]
    assert len(filled) == 1
    frame, source, _ = filled[0]
    assert 70 <= frame <= 90
    assert source in ("sens", "dense")
    assert set(hits) >= {40, 120, frame}


def test_prune_fails_open_without_serve_evidence():
    """No serve anywhere -> P1 has no anchor -> it deletes nothing.

    The direction matters: a rule that cannot find its evidence must not start guessing.
    """
    config = EventDetectionConfig()
    traj = arc_trajectory()
    dense = Dense(probabilities_with(240, [(20, 40, SMASH_TOP)]), start_frame=0)

    assert rally_span(dense, config.prune) is None

    hits = {f: ("ball", "") for f in (30, 70, 110)}
    kept, drops = prune_segment(dict(hits), traj, dense, config.prune)
    assert not [d for d in drops if d[1] == "out_of_rally"]


def test_dead_segments_only_fires_on_a_break_score():
    """0:0 and 11:x runs are warm-up / the interval; anything else is left alone."""
    # A 0:0 run: segments 0-2 are the players warming up, 3 is the real first rally.
    scores = {0: (0, 0), 1: (0, 0), 2: (0, 0), 3: (0, 0), 4: (1, 0)}
    serves = {0: False, 1: False, 2: False, 3: True, 4: True}
    assert dead_segments(scores, serves) == {0, 1, 2}

    # The same shape at 5:3 is just a rally the scoreboard had not updated for yet.
    mid = {0: (5, 3), 1: (5, 3), 2: (5, 3)}
    assert dead_segments(mid, {0: False, 1: False, 2: True}) == set()

    # A break run with no serve evidence at all: fail open.
    assert dead_segments({0: (0, 0), 1: (0, 0)}, {0: False, 1: False}) == set()


# --------------------------------------------------------------------------- #
# The stage end to end, with the dense scan pre-seeded (so: no torch, no GPU)
# --------------------------------------------------------------------------- #


@pytest.fixture
def match(tmp_path):
    """A one-segment match with every upstream artifact and a pre-seeded dense-scan cache."""
    start, n_frames = 1000, 240
    traj = arc_trajectory(n_frames=n_frames, period=40)

    write_artifact(
        PIPELINE["match_segmentation"],
        [{"start_frame": start, "end_frame": start + n_frames - 1, "start_sec": 40.0,
          "end_sec": 49.6, "duration_sec": 9.6}],
        stage_path(tmp_path, "match_segmentation") / "segments.json",
        extra={"fps": 25.0},
    )
    shuttle = []
    for method in ("inpaint", "viterbi"):
        for i, f in enumerate(traj.frames):
            shuttle.append({
                "frame": start + f, "segment_index": 0, "method": method,
                "x": traj.xs[i], "y": traj.ys[i], "visible": True, "confidence": 0.9,
            })
    write_artifact(
        PIPELINE["shuttle_tracking"], shuttle,
        stage_path(tmp_path, "shuttle_tracking") / "shuttle.json",
    )
    write_artifact(
        PIPELINE["pose"],
        [{"frame": start, "segment_index": 0, "player": "top", "keypoints": None, "bbox": None}],
        stage_path(tmp_path, "pose") / "pose.json",
    )
    for name in ("match_segmentation", "shuttle_tracking", "pose"):
        write_status(stage_path(tmp_path, name), StageState(name=name, status=StageStatus.COMPLETED))

    # A dummy checkpoint: the cache hashes the weight to decide validity, and never loads
    # it when every segment is already cached.
    checkpoint = tmp_path / "bst_fake.pt"
    checkpoint.write_bytes(b"not a real checkpoint")

    segments = [{"start_frame": start, "end_frame": start + n_frames - 1}]
    meta = dense_cache.build_meta(
        checkpoint=checkpoint, half=12, shuttle_method="inpaint", segments=segments
    )
    dense_cache.prepare(tmp_path, meta)
    probabilities = probabilities_with(
        n_frames,
        [(0, 14, SERVE_BOTTOM)] + [
            (f - 5, f + 6, SMASH_TOP if (f // 40) % 2 else SMASH_BOTTOM)
            for f in range(39, 200, 40)
        ],
    )
    dense_cache.save_segment(dense_cache.segment_file(tmp_path, 0), probabilities, start)
    return tmp_path, checkpoint, start


def test_stage_writes_absolute_frames_and_nothing_else(match):
    tmp_path, checkpoint, start = match
    module = EventDetectionModule(EventDetectionConfig(bst_checkpoint=str(checkpoint)))
    output = module.run(tmp_path)

    envelope = read_artifact(PIPELINE["event_detection"], output)
    events = envelope["events"]
    assert events, "the fixture rally should produce hits"

    # The contract is one field. Nothing has crept back in.
    assert all(set(e) == {"frame"} for e in events)

    frames = [e["frame"] for e in events]
    assert frames == sorted(frames)
    assert all(start <= f <= start + 239 for f in frames), "frames are absolute and in-segment"
    assert json.loads(output.read_text(encoding="utf-8"))["scoreboard_rule"] is False


def test_offset_is_applied_once_and_clamped(match):
    """A trajectory-sourced hit leads the true contact by ~2 frames, and is shifted for it.

    The same hit must not be shifted again anywhere else, and the shift must never push a
    hit out of its own segment.
    """
    tmp_path, checkpoint, start = match
    config = EventDetectionConfig(bst_checkpoint=str(checkpoint))
    module = EventDetectionModule(config)

    segments, fps = adapter.read_segments(tmp_path)
    results = module.detect(tmp_path, segments, None)
    raw = sorted(results[0].hits)

    output = module.run(tmp_path)
    written = [e["frame"] for e in read_artifact(PIPELINE["event_detection"], output)["events"]]

    offsets = {frame: config.offsets[source] for frame, (source, _) in results[0].hits.items()}
    expected = sorted(
        min(max(f + offsets[f], start), start + 239) for f in raw
    )
    assert written == expected
    assert all(offsets[f] in (1, 2) for f in raw)


def test_missing_scores_are_announced_not_swallowed(match, capsys):
    """The one input that may be absent is the one that could go missing unnoticed."""
    tmp_path, checkpoint, _ = match
    module = EventDetectionModule(EventDetectionConfig(bst_checkpoint=str(checkpoint)))
    module.run(tmp_path)

    out = capsys.readouterr().out
    assert "score_recognition has not run" in out
    assert "dead-time rule is OFF" in out


def test_stale_cache_is_rebuilt_not_reused(match):
    """Swap the checkpoint and the cached probabilities are no longer a valid answer."""
    tmp_path, checkpoint, _ = match
    other = tmp_path / "bst_other.pt"
    other.write_bytes(b"a different checkpoint entirely")

    segments = [{"start_frame": 1000, "end_frame": 1239}]
    meta = dense_cache.build_meta(
        checkpoint=other, half=12, shuttle_method="inpaint", segments=segments
    )
    assert dense_cache.prepare(tmp_path, meta) is False        # wiped
    assert not dense_cache.segment_file(tmp_path, 0).is_file()

    # ... and an unchanged one is kept, or every tuning run would pay for the GPU again.
    assert dense_cache.prepare(tmp_path, meta) is True


# --------------------------------------------------------------------------- #
# The DAG
# --------------------------------------------------------------------------- #


def test_event_detection_is_ordered_after_its_optional_input():
    """score_recognition is optional, but it still has to *run first* to be usable.

    Without this, a full-pipeline run would produce scores.json one stage too late and the
    dead-time rule would silently never fire on a fresh match — which is precisely the
    failure that is hard to notice.
    """
    order = pipeline_order()
    assert order.index("score_recognition") < order.index("event_detection")
    assert "score_recognition" not in PIPELINE["event_detection"].dependencies
