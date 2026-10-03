"""Review-only hit position derivation, formerly implemented in Node.

This intentionally preserves the audited TypeScript fallback order.  It reuses the
authoritative court dimensions and pose homography direction from Python.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from modules.common.court_geometry import COURT_LENGTH_M, COURT_WIDTH_M
from modules.pose.select import court_from_image

MIN_ANKLE_CONFIDENCE = 0.3
MAX_NEARBY_POSE_SECONDS = 0.12
COURT_ADJACENT_MARGIN_M = 0.8


def ankle_ground_point(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    ankles: list[list[float]] = []
    for index in (15, 16):
        if index >= len(value) or not isinstance(value[index], list):
            continue
        ankle = value[index]
        if len(ankle) < 3 or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in ankle):
            continue
        if ankle[2] >= MIN_ANKLE_CONFIDENCE:
            ankles.append(ankle)
    if not ankles:
        return None
    source = "ankle_midpoint" if len(ankles) == 2 else "single_ankle"
    confidence = min(1.0, min(float(a[2]) for a in ankles)) * (1.0 if len(ankles) == 2 else 0.65)
    return {
        "point": [sum(float(a[0]) for a in ankles) / len(ankles),
                  sum(float(a[1]) for a in ankles) / len(ankles)],
        "source": source,
        "confidence": confidence,
    }


def bbox_ground_point(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(n, (int, float)) and math.isfinite(n) for n in value):
        return None
    x1, y1, x2, y2 = map(float, value)
    return [(x1 + x2) / 2, y2] if x2 > x1 and y2 > y1 else None


def image_point_to_court(point: list[float], inverse: np.ndarray) -> dict[str, float] | None:
    projected = inverse @ np.asarray([point[0], point[1], 1.0], dtype=np.float64)
    if not np.isfinite(projected).all() or abs(projected[2]) < 1e-9:
        return None
    return {"x": float(projected[0] / projected[2] / COURT_WIDTH_M),
            "y": float(projected[1] / projected[2] / COURT_LENGTH_M)}


def distance_outside_court_m(x: float, y: float) -> float:
    dx = max(0.0, -x, x - 1.0) * COURT_WIDTH_M
    dy = max(0.0, -y, y - 1.0) * COURT_LENGTH_M
    return math.hypot(dx, dy)


def derive_court_positions(
    rallies: list[dict[str, Any]],
    court_artifact: dict[str, Any] | None,
    pose_artifact: dict[str, Any] | None,
    fps: float,
) -> None:
    """Mutate exported hits with normalized court positions and provenance."""

    inverse: np.ndarray | None = None
    courts = (court_artifact or {}).get("courts") or []
    if courts:
        try:
            inverse = court_from_image(courts[0].get("homography"))
        except (TypeError, ValueError, np.linalg.LinAlgError):
            inverse = None

    frames: dict[tuple[int, int, str], dict[str, Any]] = {}
    for row in (pose_artifact or {}).get("frames") or []:
        if (isinstance(row, dict) and isinstance(row.get("frame"), int)
                and isinstance(row.get("segment_index"), int)
                and row.get("player") in ("top", "bottom")):
            frames[(row["segment_index"], row["frame"], row["player"])] = row
    max_offset = math.floor(fps * MAX_NEARBY_POSE_SECONDS + 1e-9)

    for rally in rallies:
        for hit in rally.get("hits") or []:
            reason: str | None = None
            side = hit.get("hitterSide")
            if side is None and hit.get("hitter") and rally.get("identity"):
                side = "top" if rally["identity"]["top"] == hit["hitter"] else "bottom"
            if side is None:
                reason = "HITTER_UNRESOLVED"
            elif inverse is None:
                reason = "COURT_TRANSFORM_UNAVAILABLE"
            else:
                exact = frames.get((rally["id"], hit["frame"], side))
                nearby: list[dict[str, Any]] = []
                for offset in range(1, max_offset + 1):
                    for frame in (hit["frame"] - offset, hit["frame"] + offset):
                        sample = frames.get((rally["id"], frame, side))
                        if sample is not None:
                            nearby.append(sample)
                saw_pose = exact is not None or bool(nearby)
                saw_ground = False

                def use_point(point: list[float], source: str, source_frame: int,
                              confidence: float | None = None) -> bool:
                    nonlocal saw_ground
                    saw_ground = True
                    coordinate = image_point_to_court(point, inverse)
                    if coordinate is None or distance_outside_court_m(**coordinate) > COURT_ADJACENT_MARGIN_M:
                        return False
                    position: dict[str, Any] = {
                        **coordinate, "coordinateSpace": "court_normalized_v1",
                        "source": source, "sourceFrame": source_frame,
                    }
                    if confidence is not None:
                        position["confidence"] = confidence
                    hit["courtPosition"] = position
                    hit["positionQuality"] = "measured" if source == "ankle_midpoint" else "estimated"
                    hit["positionSource"] = source
                    return True

                exact_ankle = ankle_ground_point(exact.get("keypoints") if exact else None)
                if exact_ankle and exact_ankle["source"] == "ankle_midpoint":
                    use_point(exact_ankle["point"], "ankle_midpoint", hit["frame"], exact_ankle["confidence"])
                if "courtPosition" not in hit:
                    for sample in nearby:
                        ground = ankle_ground_point(sample.get("keypoints"))
                        if ground and ground["source"] == "ankle_midpoint" and use_point(
                            ground["point"], "nearby_frame_ankle_midpoint", sample["frame"], ground["confidence"]
                        ):
                            break
                if "courtPosition" not in hit and exact_ankle and exact_ankle["source"] == "single_ankle":
                    use_point(exact_ankle["point"], "single_ankle", hit["frame"], exact_ankle["confidence"])
                if "courtPosition" not in hit:
                    for sample in nearby:
                        ground = ankle_ground_point(sample.get("keypoints"))
                        if ground and ground["source"] == "single_ankle" and use_point(
                            ground["point"], "single_ankle", sample["frame"], ground["confidence"]
                        ):
                            break
                if "courtPosition" not in hit and exact:
                    proxy = bbox_ground_point(exact.get("bbox"))
                    if proxy:
                        use_point(proxy, "bbox_bottom_center", hit["frame"])
                if "courtPosition" not in hit:
                    reason = "POSE_UNAVAILABLE" if not saw_pose else (
                        "ANKLES_UNAVAILABLE" if not saw_ground else "OUT_OF_COURT"
                    )
            if reason:
                hit["positionQuality"] = "unresolved"
                hit["positionUnavailableReason"] = reason
