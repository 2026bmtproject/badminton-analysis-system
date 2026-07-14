"""Export ``pose.json`` as the per-segment skeleton CSV that BST consumes.

This is a *view* of the stage's artifact, not the artifact itself: the contract is
``pose.json`` (see :mod:`modules.contracts`), and this renders it into the column layout
BST's reference tooling reads, one CSV per rally segment::

    frame, player, det_idx, bbox_x1, bbox_y1, bbox_x2, bbox_y2,
    <joint>_x, <joint>_y, <joint>_s          x17 COCO joints

Two rows per frame (top, then bottom); a player who was not found in that frame gets
a row of empty cells rather than being skipped, so the row count is fixed and a
consumer can index straight into it. ``frame`` counts from 0 **within the segment**,
because that is what a clip cut from the segment will show — unlike ``pose.json``,
which uses absolute video frames so that stages can align with each other.

``det_idx`` is retained for column compatibility and is always empty: which detection
a player came from is an internal detail of the cache, and nothing downstream reads it.
"""

from __future__ import annotations

import csv
from pathlib import Path

from modules.contracts import COCO_KEYPOINTS, POSE_PLAYERS

BBOX_COLUMNS = ["bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"]


def header() -> list[str]:
    columns = ["frame", "player", "det_idx", *BBOX_COLUMNS]
    for joint in COCO_KEYPOINTS:
        columns += [f"{joint}_x", f"{joint}_y", f"{joint}_s"]
    return columns


def _row(local_frame: int, player: str, record: dict | None) -> list:
    row: list = [local_frame, player, ""]
    if record is None or record.get("keypoints") is None:
        return row + [""] * (len(BBOX_COLUMNS) + 3 * len(COCO_KEYPOINTS))
    row += [f"{v:.2f}" for v in record["bbox"]]
    for x, y, score in record["keypoints"]:
        row += [f"{x:.2f}", f"{y:.2f}", f"{score:.4f}"]
    return row


def write_segment_csv(path: str | Path, records: list[dict], start_frame: int) -> None:
    """Write one segment's records (already filtered to that segment) to ``path``."""
    by_key = {(r["frame"], r["player"]): r for r in records}
    frames = sorted({r["frame"] for r in records})

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header())
        for frame in frames:
            for player in POSE_PLAYERS:
                writer.writerow(_row(frame - start_frame, player, by_key.get((frame, player))))


def export(out_dir: str | Path, records: list[dict], segments: list[dict], stem: str) -> list[Path]:
    """Write one CSV per segment; returns the paths written."""
    grouped: dict[int, list[dict]] = {}
    for record in records:
        grouped.setdefault(record["segment_index"], []).append(record)

    written = []
    for index, segment in enumerate(segments):
        rows = grouped.get(index)
        if not rows:
            continue
        path = Path(out_dir) / f"{stem}_seg{index:04d}_skeleton.csv"
        write_segment_csv(path, rows, int(segment["start_frame"]))
        written.append(path)
    return written
