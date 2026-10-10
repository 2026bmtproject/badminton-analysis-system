"""Per-rally video overlay files for the Review player.

The Review model has to stay small, and ``pose.json`` alone runs to ~100 MB, so the
drawable geometry of each rally is written as its own compact file that the player
fetches only while overlays are switched on.  Everything is already in video pixels:
the court is projected here, so the frontend never touches a homography.

Frames are dense arrays indexed by ``frame - startFrame``; ``null`` means nothing to
draw (shuttle not visible, player not found, keypoint below the pose overlay's score
threshold, or no record at all).  Positions are whole pixels: a long rally's pose
would otherwise run to a megabyte, and sub-pixel detail is invisible on screen.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from modules.court_detection.detector import project_16
from modules.court_detection.interactive import COURT_DRAW_LINES
from modules.pose.overlay import KEYPOINT_SCORE, SKELETON

OVERLAY_SCHEMA_VERSION = "review-overlay-v1"
POSE_SIDES = ("top", "bottom")


def _xy(x: float, y: float) -> list[int]:
    return [round(float(x)), round(float(y))]


def _court_points(homography: list[list[float]]) -> list[list[int]] | None:
    points = project_16(np.asarray(homography, dtype=np.float64))
    if not np.isfinite(points).all():
        return None
    return [_xy(x, y) for x, y in points]


def _pose_entry(record: dict[str, Any]) -> dict[str, Any] | None:
    keypoints, bbox = record.get("keypoints"), record.get("bbox")
    if keypoints is None and bbox is None:
        return None
    return {
        "bbox": [round(float(v)) for v in bbox] if bbox is not None else None,
        "keypoints": ([_xy(k[0], k[1]) if k[2] >= KEYPOINT_SCORE else None for k in keypoints]
                      if keypoints is not None else None),
    }


def build_overlay_chunks(
    segments: list[dict[str, Any]],
    court: dict[str, Any] | None,
    pose: dict[str, Any] | None,
    shuttle: dict[str, Any] | None,
) -> dict[int, dict[str, Any]]:
    """One overlay document per segment that has anything to draw."""

    whole_match: list[list[int]] | None = None
    by_segment_court: dict[int, list[list[int]] | None] = {}
    for record in (court or {}).get("courts", []):
        points = _court_points(record["homography"])
        if record.get("segment_index") is None:
            whole_match = points
        else:
            by_segment_court[int(record["segment_index"])] = points

    methods = sorted({p["method"] for p in (shuttle or {}).get("points", [])})
    chunks: dict[int, dict[str, Any]] = {}
    for sid, segment in enumerate(segments):
        start, count = int(segment["start_frame"]), int(segment["end_frame"]) - int(segment["start_frame"]) + 1
        chunks[sid] = {
            "schemaVersion": OVERLAY_SCHEMA_VERSION, "segmentIndex": sid,
            "startFrame": start, "frameCount": count,
            "court": by_segment_court.get(sid, whole_match),
            "shuttle": {method: [None] * count for method in methods},
            "pose": {side: [None] * count for side in POSE_SIDES} if pose else {},
        }

    def slot(sid: int, frame: int) -> int | None:
        chunk = chunks.get(sid)
        if chunk is None:
            return None
        offset = frame - chunk["startFrame"]
        return offset if 0 <= offset < chunk["frameCount"] else None

    for point in (shuttle or {}).get("points", []):
        offset = slot(int(point["segment_index"]), int(point["frame"]))
        if offset is None or not point["visible"] or point["x"] is None or point["y"] is None:
            continue
        chunks[int(point["segment_index"])]["shuttle"][point["method"]][offset] = _xy(point["x"], point["y"])

    for record in (pose or {}).get("frames", []):
        offset = slot(int(record["segment_index"]), int(record["frame"]))
        if offset is None or record["player"] not in POSE_SIDES:
            continue
        chunks[int(record["segment_index"])]["pose"][record["player"]][offset] = _pose_entry(record)

    def drawable(chunk: dict[str, Any]) -> bool:
        return (chunk["court"] is not None
                or any(p is not None for track in chunk["shuttle"].values() for p in track)
                or any(p is not None for track in chunk["pose"].values() for p in track))

    return {sid: chunk for sid, chunk in chunks.items() if drawable(chunk)}


def write_overlay(
    directory: Path,
    url_base: str,
    segments: list[dict[str, Any]],
    court: dict[str, Any] | None,
    pose: dict[str, Any] | None,
    shuttle: dict[str, Any] | None,
    base_method: str | None,
) -> dict[str, Any] | None:
    """Write ``rally-NNN.json`` files into ``directory`` and describe them.

    The returned ``url`` ends in a content hash of every file, so a published overlay
    is immutable and a re-import with new results lands at a new address.
    """

    chunks = build_overlay_chunks(segments, court, pose, shuttle)
    if not chunks:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    for sid, chunk in sorted(chunks.items()):
        body = json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (directory / f"rally-{sid:03d}.json").write_bytes(body)
        digest.update(f"{sid}:".encode() + body)
    methods = sorted({p["method"] for p in (shuttle or {}).get("points", [])})
    return {
        "schemaVersion": OVERLAY_SCHEMA_VERSION,
        "url": f"{url_base}-{digest.hexdigest()[:16]}",
        "segments": sorted(chunks),
        "methods": methods,
        "baseMethod": base_method if base_method in methods else (methods[0] if methods else None),
        "courtLines": [list(pair) for pair in COURT_DRAW_LINES],
        "skeleton": [list(pair) for pair in SKELETON],
    }
