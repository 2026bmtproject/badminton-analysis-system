"""Stage wiring and the optional dense-scan fallback, with no models or video."""
import json

import numpy as np
import pytest

from modules.base import StageState, StageStatus, read_status, write_status
from modules.common.bst.classes import STROKE_CLASSES, UNKNOWN_INDEX
from modules.contracts import PIPELINE, PlayerIdentityEpoch, artifact_path, cache_path, stage_path
from modules.player_identity import PlayerIdentityModule
from modules.player_identity.dense_serves import (
    DENSE_CONF_MIN,
    open_dense_serves,
)
from modules.player_identity.policy import SERVE_STROKE
from modules.player_identity.policy import IdentityResult
from modules.player_identity.visual import HsvFallbackOutcome

BST_NAME = "bst_test_weight.pt"
SHUTTLE_METHOD = "inpaint"
TOP_SERVE = STROKE_CLASSES.index("Top_發短球")
BOTTOM_SERVE = STROKE_CLASSES.index("Bottom_發短球")
TOP_SMASH = STROKE_CLASSES.index("Top_殺球")


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def build_match(tmp_path, rallies, *, frames_per_segment=200):
    """A match whose row "a" is on top and wins every rally, so every serve is top."""
    match = tmp_path / "match"
    segments, scores, strokes = [], [], []
    for index in range(rallies):
        start = index * frames_per_segment
        segments.append({"start_frame": start, "end_frame": start + frames_per_segment - 1,
                         "start_sec": 0.0, "end_sec": 1.0, "duration_sec": 1.0})
        scores.append({"segment_index": index, "score_a": index, "score_b": 0,
                       "server": None, "game_index": None,
                       "sub_scores": None, "split_secs": None})
        strokes.append({"event_index": index, "frame": start + 10, "segment_index": index,
                        "player": "top", "stroke_type": SERVE_STROKE, "confidence": 0.9})
    write_json(artifact_path(match, "match_segmentation"), {"segments": segments, "fps": 25.0})
    write_json(artifact_path(match, "score_recognition"), {"rallies": scores})
    write_json(artifact_path(match, "stroke_classification"),
               {"strokes": strokes, "fps": 25.0, "shuttle_method": SHUTTLE_METHOD, "bst": BST_NAME})
    for name in PIPELINE["player_identity"].dependencies:
        write_status(stage_path(match, name),
                     StageState(name=name, status=StageStatus.COMPLETED))
    return match, segments


def serve_block(n_frames, class_index, confidence=0.9):
    """Probabilities whose winning class is ``class_index`` for every frame."""
    block = np.full((n_frames, len(STROKE_CLASSES)), 0.001, dtype=np.float32)
    block[:, UNKNOWN_INDEX] = 0.0
    block[:, class_index] = confidence
    return block


def write_scan(match, segments, blocks, *, params=None, bounds=None):
    directory = cache_path(match) / "dense_scan"
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, block in blocks.items():
        name = "s%04d.npz" % index
        np.savez(directory / name, probabilities=block,
                 start_frame=np.int64(segments[index]["start_frame"]))
        start, end = (bounds or {}).get(
            index, (segments[index]["start_frame"], segments[index]["end_frame"]))
        entries.append({"index": index, "start_frame": start, "end_frame": end,
                        "key": "k%d" % index, "file": name})
    write_json(directory / "manifest.json", {
        "format": 2,
        "params": params or {"checkpoint": BST_NAME, "shuttle_method": SHUTTLE_METHOD},
        "entries": entries,
    })
    return directory


# --------------------------------------------------------------------- stage
def test_stage_writes_epochs_and_policy_metadata(tmp_path):
    match, _ = build_match(tmp_path, 12)
    output = PlayerIdentityModule(use_dense=False).run(match)

    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert envelope["policy"]["name"] == "serve_vote_v1"
    assert envelope["dense_serves"] is None
    assert envelope["unresolved"] == []
    assert [e["top"] for e in envelope["epochs"]] == ["a"]
    assert envelope["epochs"][0]["bottom"] == "b"
    assert envelope["epochs"][0]["resolved_by"] == "vote"
    assert read_status(stage_path(match, "player_identity")).status is StageStatus.COMPLETED


def test_stage_refuses_to_run_without_its_dependencies(tmp_path):
    match, _ = build_match(tmp_path, 12)
    artifact_path(match, "score_recognition").unlink()
    with pytest.raises(FileNotFoundError):
        PlayerIdentityModule(use_dense=False).run(match)
    assert read_status(stage_path(match, "player_identity")).status is StageStatus.FAILED


def test_single_epoch_match_reports_no_convention(tmp_path):
    match, _ = build_match(tmp_path, 12)
    envelope = json.loads(
        PlayerIdentityModule(use_dense=False).run(match).read_text(encoding="utf-8"))
    # One game means no end change, so there is nothing to compare and no convention.
    assert envelope["convention"] is None


class FakeVisualFallback:
    def __init__(self):
        self.calls = []

    def resolve(self, match_path, primary):
        self.calls.append((match_path, primary))
        row = primary.unresolved[0]
        epoch = PlayerIdentityEpoch(
            row["epoch_index"], row["game_index"], row["first_segment"],
            row["last_segment"], "a", "b", row["votes"], 1.0, "hsv_default",
        )
        return HsvFallbackOutcome(
            IdentityResult([*primary.epochs, epoch], primary.convention, []),
            {"status": "resolved", "policy": "hsv_fallback_v1"},
        )


def test_resolved_primary_never_invokes_or_changes_visual_fallback(tmp_path):
    match, _ = build_match(tmp_path, 12)
    fallback = FakeVisualFallback()
    output = PlayerIdentityModule(
        use_dense=False, visual_fallback=fallback,
    ).run(match)
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert fallback.calls == []
    assert envelope["epochs"][0]["resolved_by"] == "vote"
    assert "visual_fallback" not in envelope


def test_unresolved_primary_invokes_fallback_and_persists_source(tmp_path):
    match, _ = build_match(tmp_path, 3)
    fallback = FakeVisualFallback()
    output = PlayerIdentityModule(
        use_dense=False, visual_fallback=fallback,
    ).run(match)
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert len(fallback.calls) == 1
    assert envelope["epochs"][0]["resolved_by"] == "hsv_default"
    assert envelope["visual_fallback"] == {
        "status": "resolved", "policy": "hsv_fallback_v1"
    }
    assert envelope["unresolved"] == []


# ---------------------------------------------------------------- dense scan
def test_dense_fills_a_rally_whose_first_stroke_was_not_a_serve(tmp_path):
    match, segments = build_match(tmp_path, 12)
    strokes = json.loads(artifact_path(match, "stroke_classification").read_text(encoding="utf-8"))
    strokes["strokes"][5]["stroke_type"] = "殺球"          # gate rejects rally 5
    write_json(artifact_path(match, "stroke_classification"), strokes)
    write_scan(match, segments, {5: serve_block(8, TOP_SERVE)})

    dense = open_dense_serves(match)
    assert dense is not None
    assert dense.serving_side(5) == "top"
    assert dense.describe()["source"] == "manifest"
    assert dense.serving_side(6) is None                   # no scan cached for it


def test_dense_declines_a_run_that_is_not_a_serve(tmp_path):
    match, segments = build_match(tmp_path, 12)
    write_scan(match, segments, {5: serve_block(8, TOP_SMASH)})
    assert open_dense_serves(match).serving_side(5) is None


def test_dense_declines_when_the_two_sides_are_too_close(tmp_path):
    match, segments = build_match(tmp_path, 12)
    block = serve_block(8, TOP_SERVE)
    block[:, BOTTOM_SERVE] = block[:, TOP_SERVE]           # a coin flip, not an answer
    write_scan(match, segments, {5: block})
    assert open_dense_serves(match).serving_side(5) is None


def test_dense_declines_frames_below_the_confidence_floor(tmp_path):
    match, segments = build_match(tmp_path, 12)
    write_scan(match, segments, {5: serve_block(8, TOP_SERVE, confidence=DENSE_CONF_MIN - 0.05)})
    assert open_dense_serves(match).serving_side(5) is None


def test_dense_declines_a_run_shorter_than_the_floor(tmp_path):
    match, segments = build_match(tmp_path, 12)
    write_scan(match, segments, {5: serve_block(2, TOP_SERVE)})
    assert open_dense_serves(match).serving_side(5) is None


@pytest.mark.parametrize("params", [
    {"checkpoint": "other_weight.pt", "shuttle_method": SHUTTLE_METHOD},
    {"checkpoint": BST_NAME, "shuttle_method": "viterbi"},
])
def test_a_scan_from_different_inputs_is_not_trusted(tmp_path, params):
    match, segments = build_match(tmp_path, 12)
    write_scan(match, segments, {5: serve_block(8, TOP_SERVE)}, params=params)
    assert open_dense_serves(match) is None


def test_a_scan_whose_frames_moved_is_skipped(tmp_path):
    match, segments = build_match(tmp_path, 12)
    write_scan(match, segments, {5: serve_block(8, TOP_SERVE)}, bounds={5: (0, 99)})
    assert open_dense_serves(match) is None


def test_no_scan_directory_is_a_normal_way_to_run(tmp_path):
    match, _ = build_match(tmp_path, 12)
    assert open_dense_serves(match) is None


def test_legacy_cache_layout_is_read_too(tmp_path):
    match, segments = build_match(tmp_path, 12)
    directory = cache_path(match) / "dense_scan"
    directory.mkdir(parents=True, exist_ok=True)
    np.savez(directory / "seg0005.npz", probabilities=serve_block(8, BOTTOM_SERVE),
             start_frame=np.int64(segments[5]["start_frame"]))
    write_json(directory / "meta.json", {
        "checkpoint": BST_NAME, "shuttle_method": SHUTTLE_METHOD, "half": 12,
        "segments": [[s["start_frame"], s["end_frame"]] for s in segments],
    })
    dense = open_dense_serves(match)
    assert dense.describe()["source"] == "meta"
    assert dense.serving_side(5) == "bottom"
