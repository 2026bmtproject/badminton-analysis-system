"""Read/write the segments file exchanged between pipeline stages (JSON).

Schema::

    {
      "fps": 30.0,
      "segments": [
        {"start_frame": 1, "end_frame": 19,
         "start_sec": 0.033, "end_sec": 0.633, "duration_sec": 0.6},
        ...
      ]
    }

This is the single source of truth for the format; producers (match
segmentation) and consumers (video cutter, exclude & rerun) all go through
here so the schema stays in one place.
"""

from __future__ import annotations

import json
from pathlib import Path

SEGMENT_FIELDS = ("start_frame", "end_frame", "start_sec", "end_sec", "duration_sec")


def build_segment_records(segments: list[tuple[int, int]], fps: float) -> list[dict]:
    """Turn (start_frame, end_frame) pairs into JSON-ready segment records."""
    records: list[dict] = []
    for start_frame, end_frame in segments:
        start_sec = start_frame / fps
        end_sec = end_frame / fps
        records.append({
            "start_frame": int(start_frame),
            "end_frame": int(end_frame),
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
            "duration_sec": round(end_sec - start_sec, 3),
        })
    return records


def write_segments(path: str | Path, segments: list[tuple[int, int]], fps: float) -> None:
    """Write the segments JSON, creating parent directories as needed."""
    data = {"fps": float(fps), "segments": build_segment_records(segments, fps)}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_segments(path: str | Path) -> dict:
    """Read and lightly validate a segments JSON file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"segments JSON not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("invalid segments JSON: expected an object with a 'segments' list")
    return data
