"""Recover serves the stroke gate rejected, from ``event_detection``'s dense scan.

``cache/dense_scan`` holds BST's per-frame class probabilities for every segment —
the scan ``event_detection`` runs before it prunes anything down to hits. Serves
that never survived that pruning are still in there, which is the only reason this
file exists.

It is a fallback and is treated as one. The dense scan slides a fixed window across
every frame, while the labels in ``strokes.json`` come from windows anchored between
consecutive hits; asked who hit a given stroke, the anchored answer is the better
one. Measured against each other on the rallies where both exist, they disagree
about one time in six and the anchored label is the one that is right. So this is
consulted only where the gate found nothing, and its side call has to clear a wide
margin before it is believed.

Nothing here is required. A match with no scan, a scan built from a different BST
weight, or a scan whose segments no longer line up simply yields no answers.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from modules.artifacts import read_artifact, read_segments
from modules.common.bst.classes import STROKE_CLASSES, UNKNOWN_INDEX
from modules.contracts import PIPELINE, artifact_path
from modules.event_detection.dense_cache import dense_dir, load_segment

#: A frame joins a run only if its winning class is a real stroke held this firmly.
DENSE_CONF_MIN = 0.40

#: Frames a run needs before it counts as a stroke rather than a flicker.
DENSE_MIN_RUN = 3

#: Share of the run's stroke mass that must sit on the serve classes for the run to
#: be read as a serve. The first confident stroke of a rally *should* be the serve;
#: when it is not, the rally is skipped for the same reason the stroke gate skips it.
DENSE_SERVE_MARGIN = 0.60

#: Share of the serve mass that must sit on one player before the side is believed.
#: Deliberately steep — a wrong side does not dilute the vote, it reverses it.
DENSE_SIDE_MARGIN = 0.85

_SERVE_MARK = "發"
_TOP_SERVE = [i for i, c in enumerate(STROKE_CLASSES) if c.startswith("Top_") and _SERVE_MARK in c]
_BOTTOM_SERVE = [i for i, c in enumerate(STROKE_CLASSES)
                 if c.startswith("Bottom_") and _SERVE_MARK in c]
_KNOWN = [i for i in range(len(STROKE_CLASSES)) if i != UNKNOWN_INDEX]


class DenseServes:
    """Per-segment serving side, read lazily from one match's cached scan."""

    def __init__(self, files: dict[int, Path], source: str) -> None:
        self._files = files
        self._source = source
        self._answers: dict[int, str | None] = {}

    def describe(self) -> dict:
        return {
            "source": self._source,
            "segments": len(self._files),
            "conf_min": DENSE_CONF_MIN,
            "min_run": DENSE_MIN_RUN,
            "serve_margin": DENSE_SERVE_MARGIN,
            "side_margin": DENSE_SIDE_MARGIN,
        }

    def serving_side(self, segment_index: int) -> str | None:
        if segment_index not in self._answers:
            self._answers[segment_index] = self._read(segment_index)
        return self._answers[segment_index]

    def _read(self, segment_index: int) -> str | None:
        path = self._files.get(segment_index)
        if path is None:
            return None
        probabilities, _ = load_segment(path)
        if len(probabilities) < DENSE_MIN_RUN:
            return None
        run = _first_stroke_run(probabilities)
        if run is None:
            return None
        block = probabilities[run[0]:run[1] + 1]
        serve_mass = float(block[:, _TOP_SERVE + _BOTTOM_SERVE].sum())
        known_mass = float(block[:, _KNOWN].sum())
        if known_mass <= 0 or serve_mass / known_mass < DENSE_SERVE_MARGIN:
            return None
        top = float(block[:, _TOP_SERVE].sum())
        bottom = float(block[:, _BOTTOM_SERVE].sum())
        if max(top, bottom) < DENSE_SIDE_MARGIN * (top + bottom):
            return None
        return "top" if top > bottom else "bottom"


def _first_stroke_run(probabilities: np.ndarray) -> tuple[int, int] | None:
    """The segment's first run of confident, known-class frames."""
    winners = probabilities.argmax(axis=1)
    confidence = probabilities.max(axis=1)
    usable = (winners != UNKNOWN_INDEX) & (confidence >= DENSE_CONF_MIN)
    start = None
    for index, ok in enumerate(usable):
        if not ok:
            if start is not None and index - start >= DENSE_MIN_RUN:
                return (start, index - 1)
            start = None
            continue
        if start is None:
            start = index
    if start is not None and len(usable) - start >= DENSE_MIN_RUN:
        return (start, len(usable) - 1)
    return None


def open_dense_serves(match_path: str | Path) -> DenseServes | None:
    """Open the cached scan for this match, or return None if it cannot be trusted.

    Trust here is deliberately cheap to establish: the weight *name* and shuttle
    method recorded with the scan must match what ``stroke_classification`` wrote,
    and every entry's frame range must still match the segment it claims. Verifying
    the weight's hash would mean reaching into ``models/``, which is not this stage's
    business — and the scan only ever supplies fallback votes that a wide margin has
    to clear anyway.
    """
    match_path = Path(match_path)
    directory = dense_dir(match_path)
    if not directory.is_dir():
        return None
    try:
        envelope = read_artifact(
            PIPELINE["stroke_classification"], artifact_path(match_path, "stroke_classification"))
        segments, _ = read_segments(match_path)
    except (FileNotFoundError, ValueError):
        return None

    entries = _entries(directory)
    if entries is None:
        return None
    params, rows, source = entries
    if params.get("checkpoint") != envelope.get("bst"):
        return None
    if params.get("shuttle_method") != envelope.get("shuttle_method"):
        return None

    bounds = {i: (int(s["start_frame"]), int(s["end_frame"])) for i, s in enumerate(segments)}
    files: dict[int, Path] = {}
    for index, start, end, name in rows:
        # A scan whose frames have moved is describing a different cut of the rally.
        if bounds.get(index) != (start, end):
            continue
        path = directory / name
        if path.is_file():
            files[index] = path
    return DenseServes(files, source) if files else None


def _entries(directory: Path) -> tuple[dict, list[tuple[int, int, int, str]], str] | None:
    """``(params, [(index, start, end, filename)], layout)`` for either cache layout."""
    manifest = directory / "manifest.json"
    if manifest.is_file():
        # Current layout: the manifest names the live file for each segment, which is
        # what makes it the only safe way in — superseded scans for the same frame
        # range stay on disk beside it.
        data = _load(manifest)
        if data is None:
            return None
        rows = [
            (int(e["index"]), int(e["start_frame"]), int(e["end_frame"]), str(e["file"]))
            for e in data.get("entries", [])
            if {"index", "start_frame", "end_frame", "file"} <= set(e)
        ]
        return data.get("params", {}), rows, "manifest"

    meta = directory / "meta.json"
    if meta.is_file():
        data = _load(meta)
        if data is None:
            return None
        rows = [
            (index, int(pair[0]), int(pair[1]), "seg%04d.npz" % index)
            for index, pair in enumerate(data.get("segments", []))
            if isinstance(pair, list) and len(pair) == 2
        ]
        return data, rows, "meta"
    return None


def _load(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
