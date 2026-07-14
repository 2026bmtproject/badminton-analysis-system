"""Every knob the stage has — and there are almost none, on purpose.

Compare ``event_detection.config``, which is five blocks of tuned thresholds. This stage
has no thresholds at all: BST's 25-class head *is* the answer, so there is nothing between
the model's argmax and the artifact to tune. The window it reads each hit through is fixed
by the checkpoint (see ``modules.common.bst.features.between_hits_windows``) and is
deliberately not exposed here — it is not a parameter, it is the shape of the training data.

What is left are the three things a *run* legitimately varies: which trajectory to feed the
model, which checkpoint, and what hardware to use.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrokeClassificationConfig:
    """``shuttle_method`` names one of the two trajectories in ``shuttle.json``.

    ``inpaint`` is the default for the same reason ``modules.common.bst.adapter`` prefers
    it: it is the closest thing to the TrackNetV3 output BST was trained on. ``viterbi`` is
    a different trade (sparser, less willing to invent a path), and it is worth being able
    to ask for it — but not silently.
    """

    shuttle_method: str = "inpaint"

    bst_checkpoint: str | None = None   # None -> modules.common.bst.model.DEFAULT_WEIGHT
    batch_size: int = 256
    device: str | None = None

    #: How many classes the debug CSV shows per hit. The artifact always records the top 1;
    #: the runners-up are only ever debugging material, which is why this changes no output.
    topk: int = 3
