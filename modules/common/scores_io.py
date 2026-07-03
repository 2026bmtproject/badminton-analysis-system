"""Read/write the scores file produced by the score_recognition stage (JSON).

Schema (envelope carries the model used; ``rallies`` is one record per
segment, indexing back into ``segments.json`` by ``segment_index``)::

    {
      "model": "gemini-2.5-flash",
      "rallies": [
        {"segment_index": 0, "score_a": 11, "score_b": 9,
         "server": null, "game_index": null},
        ...
      ]
    }

This is the single source of truth for the format; the score_recognition
producer and any downstream consumer (commentary) go through here so the schema
stays in one place. Mirrors ``modules.common.segments_io``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from modules.contracts import RallyScore

RALLY_FIELDS = ("segment_index", "score_a", "score_b", "server", "game_index")


def build_rally_records(rallies: list[RallyScore]) -> list[dict]:
    """Turn RallyScore dataclasses into JSON-ready records."""
    return [asdict(r) for r in rallies]


def write_scores(
    path: str | Path,
    rallies: list[RallyScore],
    model: str,
    extra: dict | None = None,
) -> None:
    """Write the scores JSON, creating parent directories as needed.

    ``extra`` is merged into the top-level envelope for optional producer
    metadata (per-attempt debug info, timings, ...) without touching the schema.
    """
    data: dict = {"model": model, "rallies": build_rally_records(rallies)}
    if extra:
        data.update(extra)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_scores(path: str | Path) -> dict:
    """Read and lightly validate a scores JSON file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"scores JSON not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("rallies"), list):
        raise ValueError("invalid scores JSON: expected an object with a 'rallies' list")
    return data
