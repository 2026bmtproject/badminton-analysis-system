"""Running BST over a list of windows, batched.

The one entry point every consumer needs. ``event_detection`` scans a rally frame by
frame (thousands of windows) and ``stroke_classification`` classifies a handful of hits
(tens) — the difference is entirely in *which* windows they ask for, so both go through
:func:`predict_windows` and the second is just the first with a short list.

Batching matters for the dense case: a single 100-frame window barely occupies the GPU, so
feeding them one at a time turns a rally scan into thousands of round trips that are
almost all latency.
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np
import torch

from modules.common.bst.classes import N_CLASSES
from modules.common.bst.features import SegmentFeatures, Window, build_window

#: Fits comfortably on an 8 GB card at SEQ_LEN=100 and is well past the point where the
#: GPU stops being starved.
DEFAULT_BATCH_SIZE = 256

ProgressFn = Callable[[float], None]


@torch.no_grad()
def predict_windows(
    model,
    features: SegmentFeatures,
    windows: Sequence[Window],
    *,
    device: str | torch.device | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: ProgressFn | None = None,
) -> np.ndarray:
    """Class probabilities for every window: ``(len(windows), 25)``, float32.

    Columns are indexed by ``classes.STROKE_CLASSES`` — so a row carries both the stroke
    and who hit it, and ``classes.TOP_INDICES`` / ``BOTTOM_INDICES`` sum it into side
    evidence. Rows are in the order the windows were given.

    ``device`` defaults to wherever the model already is, which is the only answer that
    can be right — passing anything else is asking to send CPU tensors into a CUDA model.
    """
    probabilities = np.zeros((len(windows), N_CLASSES), dtype=np.float32)
    if not windows:
        return probabilities
    if device is None:
        device = next(model.parameters()).device

    for offset in range(0, len(windows), batch_size):
        chunk = windows[offset:offset + batch_size]
        built = [build_window(features, start, end) for start, end in chunk]

        jnb = torch.from_numpy(np.stack([b[0] for b in built])).to(device)
        positions = torch.from_numpy(np.stack([b[1] for b in built])).to(device)
        shuttle = torch.from_numpy(np.stack([b[2] for b in built])).to(device)
        video_len = torch.tensor([b[3] for b in built], dtype=torch.long, device=device)

        logits = model(jnb, shuttle, positions, video_len)
        probabilities[offset:offset + len(chunk)] = (
            torch.softmax(logits, dim=-1).float().cpu().numpy()
        )
        if on_progress:
            on_progress(min(offset + len(chunk), len(windows)) / len(windows))
    return probabilities
