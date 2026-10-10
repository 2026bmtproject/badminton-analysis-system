"""Read and confirm an existing court calibration without running detection."""

from __future__ import annotations

import base64
import os
import re
import uuid
from pathlib import Path

import cv2
import numpy as np

from modules.artifacts import read_artifact, read_segments, write_artifact
from modules.base import StageStatus, artifact_fingerprint, read_status, write_status
from modules.contracts import PIPELINE, CourtCalibration, artifact_path, stage_path
from modules.court_detection import detector
from modules.court_detection.interactive import COURT_DRAW_LINES, get_default_corners, is_detection_valid, recompute_from_corners
from modules.court_detection.module import CourtDetectionConfig, CourtDetectionModule


class CourtConflict(ValueError):
    """The court changed since the UI loaded it."""


def _existing(match: Path) -> tuple[dict, list[list[float]], str]:
    status = read_status(stage_path(match, "court_detection"))
    if status is None or status.status != StageStatus.COMPLETED:
        raise FileNotFoundError("場地偵測尚未完成；請先執行既有 court_detection 階段")
    artifact = read_artifact(PIPELINE["court_detection"], artifact_path(match, "court_detection"))
    courts = artifact["courts"]
    if len(courts) != 1 or not isinstance(courts[0], dict):
        raise ValueError("court.json must contain one global calibration")
    record = courts[0]
    if record.get("segment_index") is not None:
        raise ValueError("segment-specific court calibration is not supported by this editor")
    clockwise = record.get("corners")
    if not isinstance(clockwise, list) or len(clockwise) != 4:
        raise ValueError("court.json has no four corners")
    corners = [clockwise[index] for index in (0, 1, 3, 2)]  # TL, TR, BL, BR
    if recompute_from_corners(np.asarray(corners)) is None:
        raise ValueError("court.json contains invalid corners")
    revision = artifact_fingerprint(match, "court_detection")
    if revision is None:
        raise FileNotFoundError("court.json is missing")
    return artifact, corners, revision


def _composite(match: Path, artifact: dict) -> tuple[np.ndarray, bool]:
    preview_name = artifact.get("preview_file")
    if preview_name is not None and (not isinstance(preview_name, str) or
            not re.fullmatch(r"preview-[a-f0-9]{16}\.png", preview_name)):
        raise ValueError("court.json has an invalid preview reference")
    preview_path = stage_path(match, "court_detection") / preview_name if preview_name else None
    preview = cv2.imread(str(preview_path)) if preview_path and preview_path.is_file() else None
    legacy = preview is None
    if preview is None:
        segments, _ = read_segments(match)
        indices = artifact.get("segments_used")
        if indices is None:
            indices = [idx for idx, _ in CourtDetectionModule()._pick_segments(segments)]
        if not isinstance(indices, list) or not indices or any(
            type(idx) is not int or idx < 0 or idx >= len(segments) for idx in indices
        ):
            raise ValueError("court.json has invalid preview segment indices")
        count = artifact.get("frames_per_segment", 20)
        resize = artifact.get("resize_width")
        if type(count) is not int or count < 1 or (resize is not None and (type(resize) is not int or resize < 1)):
            raise ValueError("court.json has invalid preview sampling settings")
        module = CourtDetectionModule(CourtDetectionConfig(
            frames_per_segment=count, resize_width=resize))
        preview = module._build_composite(module._resolve_input_video(match),
                                          [segments[idx] for idx in indices])
    height, width = preview.shape[:2]
    if (("preview_width" in artifact and artifact["preview_width"] != width) or
            ("preview_height" in artifact and artifact["preview_height"] != height)):
        raise ValueError("court preview dimensions do not match the calibration")
    return preview, legacy


def _points(corners: object) -> list[tuple[float, float]]:
    try:
        values = np.asarray(corners, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError("four finite corners are required") from error
    points = recompute_from_corners(values)
    if points is None:
        raise ValueError("four finite, non-degenerate corners are required")
    return points


def load_court_review(match: Path) -> dict:
    artifact, corners, revision = _existing(match)
    preview, legacy = _composite(match, artifact)
    if artifact_fingerprint(match, "court_detection") != revision:
        raise CourtConflict("場地資料已變動，請重新載入")
    ok, encoded = cv2.imencode(".png", preview)
    if not ok:
        raise ValueError("cannot encode court preview")
    height, width = preview.shape[:2]
    coordinate_unknown = "preview_width" not in artifact or "preview_height" not in artifact
    display_corners = (get_default_corners(preview.shape).tolist()
                       if coordinate_unknown else corners)
    return {"matchId": match.name, "revision": revision, "width": width, "height": height,
            "image": "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii"),
            "corners": display_corners, "points": _points(display_corners), "lines": COURT_DRAW_LINES,
            "confirmed": artifact.get("confirmed") is True,
            "detectionFailed": artifact.get("detection_failed") is True,
            "legacyPreview": legacy, "coordinateUnknown": coordinate_unknown}


def preview_corners(match: Path, revision: str, corners: object) -> dict:
    _, _, current = _existing(match)
    if current != revision:
        raise CourtConflict("場地資料已變動，請重新載入")
    return {"points": _points(corners)}


def save_corners(match: Path, revision: str, corners: object) -> dict:
    artifact, _, current = _existing(match)
    if current != revision:
        raise CourtConflict("場地資料已變動，請重新載入")
    preview, _ = _composite(match, artifact)
    points = _points(corners)
    if not is_detection_valid(points, preview.shape):
        raise ValueError("四角超出預覽影像範圍")
    if artifact_fingerprint(match, "court_detection") != revision:
        raise CourtConflict("場地資料已變動，請重新載入")
    tl, tr, bl, br = points[:4]
    homography = detector.homography_from_corners(np.float32([tl, tr, bl, br]))
    if homography is None or not np.isfinite(homography).all():
        raise ValueError("無法從四角建立 homography")
    record = CourtCalibration(corners=[list(tl), list(tr), list(br), list(bl)],
                              homography=homography.tolist(), segment_index=None)
    output = artifact_path(match, "court_detection")
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    metadata = {key: value for key, value in artifact.items() if key != "courts"}
    metadata["confirmed"] = True
    try:
        write_artifact(PIPELINE["court_detection"], [record], temporary, extra=metadata)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    status = read_status(stage_path(match, "court_detection"))
    if status is not None:
        write_status(stage_path(match, "court_detection"), status)
    return {"revision": artifact_fingerprint(match, "court_detection"),
            "confirmed": True, "detectionFailed": metadata.get("detection_failed") is True,
            "corners": [list(p) for p in points[:4]], "points": points}
