"""BST (Badminton Stroke-type Transformer) — the model shared by two stages.

``event_detection`` and ``stroke_classification`` both run the same network over the same
25-class head, so it lives here rather than inside either of them:

* **event_detection** scans every frame of a rally and reads the *side* evidence out of
  the class probabilities (``TOP_INDICES`` / ``BOTTOM_INDICES``) to decide who hit and
  when;
* **stroke_classification** takes the hits that stage found and reads the *stroke* out of
  the same probabilities.

What is here is only the model and the geometry it eats. The heuristics built on top of
its output — fusing side evidence over a window, locking regions, deciding what counts as
a hit — belong to the stage that has an opinion about them, and are deliberately not
shared. That includes *which* windows to ask about: a stage passes its own list in. The
one exception is :func:`between_hits_windows`, which is not an opinion but the segmentation
the checkpoint was trained under, and so is part of the model's contract rather than any
stage's.

Typical use::

    from modules.common.bst import adapter, between_hits_windows, load_bst_model, predict_windows

    features = adapter.load_segment_features(match_path)[0]
    model = load_bst_model(device="cuda")
    windows = between_hits_windows(hits, len(features), fps)   # or any [start, end) list
    probs = predict_windows(model, features, windows, device="cuda")   # (n_windows, 25)
"""

from modules.common.bst.classes import (
    BASE12_TO_8,
    BOTTOM_INDICES,
    CLASSES_8,
    IN_DIM,
    N_CLASSES,
    SEQ_LEN,
    STROKE_CLASSES,
    TOP_INDICES,
    UNKNOWN_INDEX,
    to8,
    to_base,
    to_side,
)
from modules.common.bst.features import (
    SegmentFeatures,
    Window,
    between_hits_windows,
    build_window,
)
from modules.common.bst.inference import predict_windows
from modules.common.bst.model import BST_CG_AP, build_bst_model, default_device, load_bst_model

__all__ = [
    "BASE12_TO_8",
    "BOTTOM_INDICES",
    "BST_CG_AP",
    "CLASSES_8",
    "IN_DIM",
    "N_CLASSES",
    "SEQ_LEN",
    "STROKE_CLASSES",
    "SegmentFeatures",
    "TOP_INDICES",
    "UNKNOWN_INDEX",
    "Window",
    "between_hits_windows",
    "build_bst_model",
    "build_window",
    "default_device",
    "load_bst_model",
    "predict_windows",
    "to8",
    "to_base",
    "to_side",
]
