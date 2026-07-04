"""Court-detection stage: the pieces that don't need a real video or a GUI.

Covered:
  * contract/registration wiring (the stage's spec and module attributes),
  * ``_pick_segments`` — the "longest N segments" selection,
  * geometry helpers ``recompute_from_corners`` / ``is_detection_valid``,
  * ``CourtDetectionModule.run`` orchestration with the video decode, the
    detector and the interactive confirm step all stubbed out — this pins the
    tricky bits: the TL,TR,BL,BR -> clockwise-from-top-left corner reorder, the
    confirm-callback wiring, and the detection-failed fallback.

Nothing here decodes a frame, runs the detector, or opens a window.
"""

from __future__ import annotations

import numpy as np
import pytest

from modules.artifacts import read_artifact
from modules.contracts import PIPELINE, CourtCalibration
from modules.court_detection import detector
from modules.court_detection import module as court_module
from modules.court_detection.interactive import (
    is_detection_valid,
    recompute_from_corners,
)
from modules.court_detection.module import CourtDetectionConfig, CourtDetectionModule
from modules.match_segmentation.segments import write_segments


# --------------------------------------------------------------------------- #
# contract / registration wiring
# --------------------------------------------------------------------------- #


def test_court_detection_is_the_declared_stage_contract():
    spec = PIPELINE["court_detection"]
    assert spec.record_type is CourtCalibration
    assert spec.output_filename == "court.json"
    assert spec.record_key == "courts"
    assert spec.dependencies == ["match_segmentation"]


def test_module_attributes_track_the_spec():
    m = CourtDetectionModule()
    assert m.name == "court_detection"
    assert m.dependencies == PIPELINE["court_detection"].dependencies


def test_module_is_registered_in_the_runner():
    from modules.runner import available_modules

    assert "court_detection" in available_modules()


# --------------------------------------------------------------------------- #
# _pick_segments — pick the longest N segments (most stable court view)
# --------------------------------------------------------------------------- #


def _seg(start: int, end: int) -> dict:
    return {"start_frame": start, "end_frame": end, "duration_sec": (end - start) / 30.0}


def test_pick_segments_takes_the_longest_n():
    segments = [
        _seg(0, 100),     # dur 100
        _seg(200, 210),   # dur 10
        _seg(300, 500),   # dur 200  (longest)
        _seg(600, 660),   # dur 60
    ]
    m = CourtDetectionModule(CourtDetectionConfig(num_segments=3))
    picked = m._pick_segments(segments)

    spans = [s["end_frame"] - s["start_frame"] for s in picked]
    assert spans == [200, 100, 60]  # sorted longest-first


def test_pick_segments_returns_all_when_fewer_than_requested():
    segments = [_seg(0, 100), _seg(200, 260)]
    m = CourtDetectionModule(CourtDetectionConfig(num_segments=3))
    assert len(m._pick_segments(segments)) == 2


def test_pick_segments_falls_back_to_frame_span_without_duration():
    # No duration_sec field -> selection uses (end_frame - start_frame).
    segments = [
        {"start_frame": 0, "end_frame": 50},
        {"start_frame": 100, "end_frame": 400},
    ]
    m = CourtDetectionModule(CourtDetectionConfig(num_segments=1))
    (picked,) = m._pick_segments(segments)
    assert picked["start_frame"] == 100


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #


def test_recompute_from_corners_maps_corner_coords_back_to_the_corners():
    # The first four projected points are the four court corners, so they must
    # come back exactly on the pixel corners fed in (TL, TR, BL, BR).
    corners = [(10.0, 10.0), (90.0, 10.0), (10.0, 90.0), (90.0, 90.0)]
    pts = recompute_from_corners(np.float32(corners))

    assert pts is not None and len(pts) == 16
    for got, want in zip(pts[:4], corners):
        assert got[0] == pytest.approx(want[0], abs=1e-3)
        assert got[1] == pytest.approx(want[1], abs=1e-3)


def test_recompute_from_corners_degenerate_returns_none():
    # All four corners collapsed to a point -> no valid homography.
    assert recompute_from_corners(np.float32([[5, 5]] * 4)) is None


def _sixteen(corners):
    """A 16-point list whose first four are ``corners`` (rest are fillers)."""
    return list(corners) + [(50.0, 50.0)] * 12


def test_is_detection_valid_accepts_in_bounds_corners():
    pts = _sixteen([(10, 10), (90, 10), (10, 90), (90, 90)])
    assert is_detection_valid(pts, (100, 100, 3)) is True


def test_is_detection_valid_rejects_out_of_bounds_corner():
    pts = _sixteen([(10, 10), (300, 10), (10, 90), (90, 90)])  # 300 >> 100+margin
    assert is_detection_valid(pts, (100, 100, 3)) is False


def test_is_detection_valid_rejects_missing_points():
    assert is_detection_valid(None, (100, 100, 3)) is False
    assert is_detection_valid([(0, 0)], (100, 100, 3)) is False


# --------------------------------------------------------------------------- #
# run() orchestration — video decode, detector and confirm all stubbed
# --------------------------------------------------------------------------- #


def _make_match(tmp_path, segments):
    """A minimal match dir: an input video file + a match_segmentation artifact."""
    match = tmp_path / "M"
    (match / "input").mkdir(parents=True)
    (match / "input" / "match.mp4").write_bytes(b"")  # never actually decoded
    seg_dir = match / "stages" / "match_segmentation"
    seg_dir.mkdir(parents=True)
    write_segments(seg_dir / "segments.json", segments, fps=30.0)
    return match


def _stub_pipeline(monkeypatch, detect_return):
    """Stub frame decode + composite + detector so run() needs no real video."""
    monkeypatch.setattr(
        court_module, "extract_frames_in_range",
        lambda *a, **k: [np.zeros((100, 100, 3), np.uint8)] * 5,
    )
    monkeypatch.setattr(
        court_module, "composite_median",
        lambda frames: np.zeros((100, 100, 3), np.uint8),
    )
    monkeypatch.setattr(detector, "detect", lambda img: detect_return)


def test_run_writes_corners_clockwise_from_top_left(tmp_path, monkeypatch):
    # detector emits TL, TR, BL, BR; the artifact must reorder to TL, TR, BR, BL.
    tl, tr, bl, br = (10, 10), (90, 10), (10, 90), (90, 90)
    detected = np.array([tl, tr, bl, br] + [(0, 0)] * 12, dtype=np.float32)

    match = _make_match(tmp_path, [(0, 100), (200, 210), (300, 500), (600, 660)])
    _stub_pipeline(monkeypatch, detected)

    out = CourtDetectionModule(CourtDetectionConfig(num_segments=3)).run(match)

    data = read_artifact(PIPELINE["court_detection"], out)
    (record,) = data["courts"]
    assert record["corners"] == [list(tl), list(tr), list(br), list(bl)]
    assert len(record["homography"]) == 3 and len(record["homography"][0]) == 3
    assert record["segment_index"] is None
    assert data["segments_used"] == [2, 0, 3]   # the three longest, longest-first
    assert data["detection_failed"] is False
    assert data["confirmed"] is False


def test_run_uses_the_confirm_callback_result(tmp_path, monkeypatch):
    detected = np.array([(10, 10), (90, 10), (10, 90), (90, 90)] + [(0, 0)] * 12,
                        dtype=np.float32)
    match = _make_match(tmp_path, [(0, 100), (300, 500)])
    _stub_pipeline(monkeypatch, detected)

    seen = {}

    def confirm(image, pts, is_manual):
        seen["image_shape"] = image.shape
        seen["n_pts"] = len(pts)
        seen["is_manual"] = is_manual
        # user drags the court to a different square (TL, TR, BL, BR)
        return [(0.0, 0.0), (80.0, 0.0), (0.0, 80.0), (80.0, 80.0)] + [(0.0, 0.0)] * 12

    out = CourtDetectionModule().run(match, confirm=confirm)

    # the callback was handed the composite image and the 16 auto points
    assert seen == {"image_shape": (100, 100, 3), "n_pts": 16, "is_manual": False}
    data = read_artifact(PIPELINE["court_detection"], out)
    (record,) = data["courts"]
    # reordered clockwise: TL, TR, BR, BL
    assert record["corners"] == [[0.0, 0.0], [80.0, 0.0], [80.0, 80.0], [0.0, 80.0]]
    assert data["confirmed"] is True


def test_run_falls_back_to_manual_when_detection_fails(tmp_path, monkeypatch):
    match = _make_match(tmp_path, [(0, 100), (300, 500)])
    _stub_pipeline(monkeypatch, None)  # detector returns None

    out = CourtDetectionModule().run(match)

    data = read_artifact(PIPELINE["court_detection"], out)
    assert data["detection_failed"] is True
    # fallback corners are the image corners (100x100), reordered clockwise.
    # (they round-trip through a homography, so allow tiny float error)
    (record,) = data["courts"]
    expected = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
    for got, want in zip(record["corners"], expected):
        assert got == pytest.approx(want, abs=1e-3)


def test_run_marks_status_completed(tmp_path, monkeypatch):
    from modules.base import StageStatus, read_status
    from modules.contracts import stage_path

    detected = np.array([(10, 10), (90, 10), (10, 90), (90, 90)] + [(0, 0)] * 12,
                        dtype=np.float32)
    match = _make_match(tmp_path, [(0, 100), (300, 500)])
    _stub_pipeline(monkeypatch, detected)

    CourtDetectionModule().run(match)

    state = read_status(stage_path(match, "court_detection"))
    assert state is not None
    assert state.status == StageStatus.COMPLETED
    assert state.output_path.endswith("court.json")
