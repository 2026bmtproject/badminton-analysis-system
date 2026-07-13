"""Heatmap -> one shuttle position per frame. The step both trackers build on.

This is TrackNetV3's own read-out: binarize the heatmap at a confidence
threshold, take the largest responding blob, call its bounding-box centre the
shuttle. It is deliberately dumb — one candidate per frame, no temporal
reasoning, nothing to recover a frame the network missed.

Both trackers in this package start here, for different reasons:

* ``track_inpaint`` treats this as *the* trajectory and hands the gaps to
  InpaintNet.
* ``track_viterbi`` treats it as a low-confidence fallback candidate, to be
  considered alongside the weaker blobs it extracts itself.

The canonical trajectory representation across this package is ``(xy, conf)``:
``xy`` is ``(T, 2)`` float in **source-video pixels** with NaN marking "no
position", and ``conf`` is ``(T,)`` in [0, 1], zero wherever ``xy`` is NaN.
NaN — rather than the reference implementation's ``(0, 0)`` — because the top-left
pixel is a legitimate coordinate, and conflating it with "missing" is a bug
waiting to happen.
"""

from __future__ import annotations

import cv2
import numpy as np

#: Default confidence for binarizing the heatmap (TrackNetV3's own default).
DEFAULT_THRESHOLD = 0.5


def image_scaler(
    img_shape: tuple[int, int],
    hm_shape: tuple[int, int] = (512, 288),
) -> tuple[float, float]:
    """Factors mapping heatmap pixels to source-video pixels."""
    (img_w, img_h), (hm_w, hm_h) = img_shape, hm_shape
    return img_w / hm_w, img_h / hm_h


def largest_blob(binary: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box of the largest connected region of a 0/255 image, or None."""
    if not binary.any():
        return None
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    boxes = [cv2.boundingRect(c) for c in contours]
    return max(boxes, key=lambda b: b[2] * b[3])


def baseline_track(
    heatmaps: np.ndarray,
    img_shape: tuple[int, int],
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame largest-blob centre: ``(xy (T, 2) with NaN, conf (T,))``.

    ``heatmaps`` is ``(T, H, W)`` uint8 (confidence * 255); coordinates come back
    in source-video pixels.
    """
    num_frames, hm_h, hm_w = heatmaps.shape
    w_scale, h_scale = image_scaler(img_shape, (hm_w, hm_h))
    cutoff = threshold * 255.0

    xy = np.full((num_frames, 2), np.nan, dtype=float)
    conf = np.zeros(num_frames, dtype=float)

    for t in range(num_frames):
        frame = heatmaps[t]
        binary = ((frame > cutoff) * 255).astype(np.uint8)
        box = largest_blob(binary)
        if box is None:
            continue
        x, y, bw, bh = box
        xy[t] = ((x + bw / 2) * w_scale, (y + bh / 2) * h_scale)
        conf[t] = float(frame[y : y + bh, x : x + bw].max()) / 255.0

    return xy, conf
