"""One rally's hits -> one :class:`StrokeLabel` each.

The whole stage in one function. There is no heuristic layer here — no fusing, no
thresholds, no gap filling — because BST answers the question directly: each hit gets one
window, one forward pass, and the argmax of 25 classes is both the stroke and the hitter.

A :class:`Prediction` keeps the full probability row alongside the label it produced. The
artifact only ever carries the argmax, but the row is what makes a wrong answer legible
afterwards (was it a coin-flip between 殺球 and 切球, or did the model have no idea?), so
it is kept until the debug CSV has had its chance at it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from modules.common.bst import SegmentFeatures, Window, between_hits_windows, predict_windows
from modules.common.bst.classes import STROKE_CLASSES, UNKNOWN_CLASS, to8, to_base, to_side
from modules.contracts import StrokeLabel


@dataclass(frozen=True)
class Prediction:
    """One hit: what BST said, and everything needed to explain why."""

    label: StrokeLabel
    local_frame: int             # the hit, as an index into the segment
    window: Window               # the local [start, end) BST read it through
    probabilities: np.ndarray    # (25,) — the full row, not just the winner


def label_of(
    probabilities: np.ndarray, *, event_index: int, frame: int, segment_index: int
) -> StrokeLabel:
    """The winning class, translated into the contract's terms.

    Class 0 (``未知球種``) is a real answer and is recorded as one: ``stroke_type`` says
    ``未知球種`` and ``player`` is None. Taking the best of the 24 *known* strokes instead
    would turn "I cannot read this hit" into a specific claim about it, at whatever
    confidence the runner-up happened to have — the one failure mode that would be
    invisible downstream.
    """
    index = int(np.argmax(probabilities))
    name = STROKE_CLASSES[index]
    stroke = to8(to_base(name))
    return StrokeLabel(
        event_index=event_index,
        frame=frame,
        segment_index=segment_index,
        player=to_side(name),                     # None exactly when stroke is None
        stroke_type=stroke if stroke is not None else UNKNOWN_CLASS,
        confidence=float(probabilities[index]),
    )


def classify_segment(
    model,
    features: SegmentFeatures,
    hits: list[tuple[int, int]],
    fps: float,
    segment_index: int,
    *,
    device: str | None = None,
    batch_size: int = 256,
) -> list[Prediction]:
    """Classify every hit in one rally.

    ``hits`` are ``(event_index, local_frame)`` pairs — the event index comes from
    ``events.json`` and is carried through untouched, so the records this produces stay
    aligned with it no matter what order the hits arrive in.
    """
    if not hits:
        return []

    # between_hits_windows sorts, so the hits have to be sorted alongside it or every
    # window would be paired with the wrong hit.
    ordered = sorted(hits, key=lambda pair: pair[1])
    windows = between_hits_windows([frame for _, frame in ordered], len(features), fps)

    probabilities = predict_windows(
        model, features, windows, device=device, batch_size=batch_size
    )
    return [
        Prediction(
            label=label_of(
                row,
                event_index=event_index,
                frame=features.start_frame + local_frame,
                segment_index=segment_index,
            ),
            local_frame=local_frame,
            window=window,
            probabilities=row,
        )
        for (event_index, local_frame), window, row in zip(ordered, windows, probabilities)
    ]
