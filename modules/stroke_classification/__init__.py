"""球種辨識 — what each hit was, and who hit it.

``events.json`` says a hit happened at frame N. This stage reads BST through the window
that hit lives in and turns it into a stroke: one of the 8 classes users are shown, plus
the hitter, plus a confidence. Out comes ``strokes.json``, one record per hit.

The model is the shared ``modules.common.bst``, the same weights ``event_detection``
already ran — but through *different windows*, which is the entire content of this stage.
See ``module.py``.
"""

from modules.stroke_classification.config import StrokeClassificationConfig
from modules.stroke_classification.module import StrokeClassificationModule

__all__ = ["StrokeClassificationConfig", "StrokeClassificationModule"]
