"""Generic read/write for stage artifacts, driven by :class:`StageSpec`.

Every stage writes exactly one JSON artifact that follows the same envelope::

    {"<record_key>": [ {<record fields>}, ... ], ...optional metadata... }

Because the shape is uniform, a single reader/writer serves every stage: the
:class:`~modules.contracts.StageSpec` supplies the envelope key
(``record_key``) and the record dataclass (``record_type``), so a new stage
needs only a dataclass and a ``PIPELINE`` entry — not its own I/O module.

Direction matters: the module that *reads* an artifact is usually a different
stage than the one that *wrote* it, so I/O belongs to the contract (this file),
not to the producing module. Consumers therefore read through
``read_artifact(PIPELINE[stage], path)`` rather than importing the producer's
package.

Stage-specific *derivation* (e.g. turning frame indices into seconds) is not
serialization and stays with its producer — see
``modules.match_segmentation.segments.build_segment_records``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from modules.contracts import PIPELINE, StageSpec, stage_path


def _as_record(item: Any) -> dict:
    """Normalize a record (dataclass instance or plain dict) to a dict."""
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    return dict(item)


def write_artifact(
    spec: StageSpec,
    records: list,
    path: str | Path,
    extra: dict | None = None,
) -> None:
    """Write ``records`` as ``spec``'s artifact JSON, creating parent dirs.

    ``records`` may be ``spec.record_type`` instances or plain dicts. ``extra``
    is merged into the top-level envelope for producer metadata (fps, model,
    timings, ...) without touching the record schema.
    """
    envelope: dict = {spec.record_key: [_as_record(r) for r in records]}
    if extra:
        envelope.update(extra)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2)


def read_artifact(spec: StageSpec, path: str | Path) -> dict:
    """Read and lightly validate ``spec``'s artifact JSON; return the envelope.

    Raises ``FileNotFoundError`` if the file is missing and ``ValueError`` if it
    is not an object carrying a ``spec.record_key`` list.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"{spec.name} artifact not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get(spec.record_key), list):
        raise ValueError(
            f"invalid {spec.name} artifact: expected an object with a "
            f"{spec.record_key!r} list"
        )
    return data


def read_records(spec: StageSpec, path: str | Path) -> list[dict]:
    """Convenience: just the record list from ``spec``'s artifact."""
    return read_artifact(spec, path)[spec.record_key]


def read_segments(match_path: str | Path) -> tuple[list[dict], float]:
    """``segments.json``: the rally segments and the fps they were cut at.
    """
    spec = PIPELINE["match_segmentation"]
    envelope = read_artifact(spec, stage_path(match_path, spec.name) / spec.output_filename)
    segments = envelope[spec.record_key]
    if not segments:
        raise RuntimeError("no segments in match_segmentation output")
    fps = envelope.get("fps")
    if not fps:
        raise RuntimeError("match_segmentation output carries no fps")
    return segments, float(fps)
