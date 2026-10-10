"""Court editor transport uses the existing calibration math, without detection."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from modules.artifacts import read_artifact, write_artifact
from modules.base import StageState, StageStatus, artifact_fingerprint, read_status, write_status
from modules.contracts import PIPELINE, CourtCalibration, artifact_path, stage_path
from modules.court_detection import detector
from modules.court_detection.review import CourtConflict, load_court_review, preview_corners, save_corners
from modules.local_tasks.service import TaskManager, WorkerBusy, handler_for
from modules.local_tasks.planning import build_plan
from modules.runner import available_modules


def court_match(tmp_path, *, preview=True, failed=True):
    match = tmp_path / "matches" / "Sample"
    stage = stage_path(match, "court_detection")
    stage.mkdir(parents=True)
    corners = [[10., 10.], [90., 10.], [10., 90.], [90., 90.]]
    H = detector.homography_from_corners(np.float32(corners))
    metadata = {"confirmed": False, "detection_failed": failed,
                "segments_used": [0], "frames_per_segment": 3}
    if preview:
        preview_name = "preview-0123456789abcdef.png"
        assert cv2.imwrite(str(stage / preview_name), np.zeros((100, 100, 3), np.uint8))
        metadata.update(preview_width=100, preview_height=100, resize_width=None,
                        preview_file=preview_name)
    write_artifact(PIPELINE["court_detection"], [CourtCalibration(
        corners=[corners[i] for i in (0, 1, 3, 2)], homography=H.tolist())],
        artifact_path(match, "court_detection"), extra=metadata)
    write_status(stage, StageState(name="court_detection", status=StageStatus.COMPLETED, inputs={}))
    return match


def test_editor_draft_and_save_reuse_geometry_and_preserve_detection_history(tmp_path):
    match = court_match(tmp_path)
    original = artifact_fingerprint(match, "court_detection")
    view = load_court_review(match)
    assert view["corners"] == [[10., 10.], [90., 10.], [10., 90.], [90., 90.]]
    assert (view["width"], view["height"]) == (100, 100)
    assert view["confirmed"] is False and view["detectionFailed"] is True
    assert len(view["lines"]) == 10
    changed = [[12., 12.], [88., 12.], [12., 88.], [88., 88.]]
    draft = preview_corners(match, original, changed)
    assert np.asarray(draft["points"][:4]) == pytest.approx(np.asarray(changed))
    assert artifact_fingerprint(match, "court_detection") == original
    saved = save_corners(match, original, changed)
    assert saved["confirmed"] is True and saved["detectionFailed"] is True
    assert saved["revision"] != original
    data = read_artifact(PIPELINE["court_detection"], artifact_path(match, "court_detection"))
    assert np.asarray(data["courts"][0]["corners"]) == pytest.approx(np.asarray([changed[i] for i in (0, 1, 3, 2)]))
    assert np.asarray(data["courts"][0]["homography"]) == pytest.approx(
        detector.homography_from_corners(np.float32(changed)))
    assert data["segments_used"] == [0] and data["detection_failed"] is True
    assert read_status(stage_path(match, "court_detection")).status == StageStatus.COMPLETED
    with pytest.raises(CourtConflict):
        save_corners(match, original, changed)


def test_editor_rejects_invalid_or_changed_corners(tmp_path):
    match = court_match(tmp_path)
    revision = artifact_fingerprint(match, "court_detection")
    for corners in ([[[1, 1]] * 4], [[0, 0], [1000, 0], [0, 100], [1000, 100]],
                    [[0, 0], [float("nan"), 0], [0, 100], [100, 100]]):
        with pytest.raises(ValueError):
            save_corners(match, revision, corners)
    assert artifact_fingerprint(match, "court_detection") == revision
    data = read_artifact(PIPELINE["court_detection"], artifact_path(match, "court_detection"))
    data["other_writer"] = True
    artifact_path(match, "court_detection").write_text(json.dumps(data))
    with pytest.raises(CourtConflict):
        preview_corners(match, revision, [[10, 10], [90, 10], [10, 90], [90, 90]])


def test_legacy_preview_reuses_composite_without_detector(tmp_path, monkeypatch):
    match = court_match(tmp_path, preview=False)
    from modules.court_detection.module import CourtDetectionModule
    monkeypatch.setattr("modules.court_detection.review.read_segments", lambda _: ([{"start_frame": 0, "end_frame": 90}], 30.))
    monkeypatch.setattr(CourtDetectionModule, "_resolve_input_video", lambda self, _: match / "input" / "match.mp4")
    monkeypatch.setattr(CourtDetectionModule, "_build_composite", lambda self, video, segments: np.zeros((100, 100, 3), np.uint8))
    monkeypatch.setattr(detector, "detect", lambda _: pytest.fail("detection must not run"))
    result = load_court_review(match)
    assert result["legacyPreview"] is True
    assert result["coordinateUnknown"] is True
    assert np.asarray(result["corners"]) == pytest.approx(np.asarray([[30., 25.], [70., 25.], [15., 85.], [85., 85.]]))
    assert result["image"].startswith("data:image/png;base64,")


def test_service_blocks_save_while_same_match_is_active(tmp_path):
    match = court_match(tmp_path)
    manager = TaskManager(match.parent, tmp_path / "tasks")
    view = manager.court("Sample")
    request = {"matchId": "Sample", "revision": view["revision"], "corners": view["corners"]}
    manager.active_id = "a" * 32
    manager.store.save({"id": manager.active_id, "matchId": "Sample", "status": "running", "createdAt": "2026-01-01",
                        "currentStage": None})
    assert manager.court("Sample")["saveBlocked"] is True
    with pytest.raises(WorkerBusy):
        manager.court_save(request)
    assert artifact_fingerprint(match, "court_detection") == view["revision"]
    with pytest.raises(ValueError, match="matchId"):
        manager.court("../Sample")


def test_unconfirmed_court_does_not_gate_downstream_plan(tmp_path):
    match = court_match(tmp_path)
    plan = build_plan(match, ["pose"], "continue", registry=available_modules())
    assert "court_detection" in plan["requiredStages"]
    assert any(row["name"] == "pose" and row["action"] == "run" for row in plan["stages"])


def test_manual_save_reports_existing_downstream_stale_policy(tmp_path):
    match = court_match(tmp_path)
    manager = TaskManager(match.parent, tmp_path / "tasks")
    revision = manager.court("Sample")["revision"]
    write_status(stage_path(match, "pose"), StageState(name="pose", status=StageStatus.COMPLETED,
        inputs={"court_detection": revision}))
    write_status(stage_path(match, "shuttle_tracking"), StageState(name="shuttle_tracking",
        status=StageStatus.COMPLETED, inputs=None))
    result = manager.court_save({"matchId": "Sample", "revision": revision,
        "corners": [[12, 12], [88, 12], [12, 88], [88, 88]]})
    assert "pose" in result["staleStages"]
    assert "shuttle_tracking" in result["unknownStages"]


def test_court_http_preview_save_and_origin_policy(tmp_path):
    match = court_match(tmp_path)
    manager = TaskManager(match.parent, tmp_path / "tasks")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(manager,
        "http://127.0.0.1:5173", "127.0.0.1", 0))
    server.RequestHandlerClass = handler_for(manager, "http://127.0.0.1:5173",
        "127.0.0.1", server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def call(method, path, body=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result
    try:
        status, view = call("GET", "/api/pipeline/court?matchId=Sample")
        assert status == 200 and view["confirmed"] is False
        payload = {"matchId": "Sample", "revision": view["revision"], "corners": view["corners"]}
        assert call("POST", "/api/pipeline/court/save", payload, "http://evil.example")[0] == 403
        assert call("POST", "/api/pipeline/court/preview", payload, "http://127.0.0.1:5173")[0] == 200
        assert call("POST", "/api/pipeline/court/save", payload, "http://127.0.0.1:5173")[0] == 200
        assert call("POST", "/api/pipeline/court/save", payload, "http://127.0.0.1:5173")[0] == 409
        assert call("GET", "/api/pipeline/court?matchId=../Sample")[0] == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
