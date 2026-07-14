"""The per-hit detail CSV, behind ``--debug-csv``.

``strokes.json`` carries the winning class and its confidence, which is the right contract
and tells you nothing about *why*. This writes the row behind it: the window BST actually
read, the runners-up, and the three numbers that separate the two ways a prediction goes
wrong — a coin-flip between two similar strokes (top-1 and top-2 close together), and the
model not recognising a hit at all (``p_unknown`` high).

``p_top`` / ``p_bottom`` are the same side evidence ``event_detection`` fuses its hitter
from, summed straight out of this hit's own row. When the two stages disagree about who
hit a shuttle, these are the numbers that say so.

One file for the whole match, one row per hit, in event order. UTF-8 with a BOM so Excel
opens the Chinese class names without mangling them.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

import numpy as np

from modules.common.bst.classes import BOTTOM_INDICES, STROKE_CLASSES, TOP_INDICES, UNKNOWN_INDEX
from modules.stroke_classification.predict import Prediction

COLUMNS = [
    "Event", "Frame", "Segment", "LocalFrame", "WinStart", "WinEnd", "WinLen",
    "Player", "Stroke", "Confidence", "RawClass", "p_unknown", "p_top", "p_bottom",
]


def write_csv(path: str | Path, predictions: Sequence[Prediction], topk: int = 3) -> None:
    """Write one row per hit, with the top ``topk`` classes spelled out."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    columns = [*COLUMNS, *(f"Top{i}" for i in range(1, topk + 1))]
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for prediction in predictions:
            writer.writerow(_row(prediction, topk))


def _row(prediction: Prediction, topk: int) -> list:
    label = prediction.label
    probabilities = prediction.probabilities
    start, end = prediction.window

    ranked = np.argsort(probabilities)[::-1][:topk]
    return [
        label.event_index,
        label.frame,
        label.segment_index,
        prediction.local_frame,
        start,
        end,
        end - start,
        label.player or "",
        label.stroke_type,
        f"{label.confidence:.4f}",
        STROKE_CLASSES[int(np.argmax(probabilities))],   # the un-merged 25-class name
        f"{probabilities[UNKNOWN_INDEX]:.4f}",
        f"{probabilities[list(TOP_INDICES)].sum():.4f}",
        f"{probabilities[list(BOTTOM_INDICES)].sum():.4f}",
        *(f"{STROKE_CLASSES[i]}({probabilities[i]:.0%})" for i in ranked),
    ]
