"""Run TrackNet over one rally segment and return its raw heatmaps.

Two things here matter more than they look:

**Frames are resized on read.** A 1080p frame is 6 MB; a whole match is hundreds
of GB, and even a single 30 s rally is ~5.6 GB at source resolution. The network
input is 512x288 regardless, so frames are shrunk the moment they are decoded and
the full-resolution image is never held. That bounds a segment's frame buffer to
~400 MB. This is only safe because the released checkpoint uses
``bg_mode="concat"``, where the reference pipeline also does every operation at
512x288 (the median is resized before use, and frames are merely resized). The
``subtract`` modes instead difference frames at source resolution, which resizing
early would change — so they are rejected rather than silently approximated.

**Resizing uses PIL, not cv2.** The reference dataset pipeline resized with
``PIL.Image.resize`` and the checkpoint was trained on the result; PIL's filter
is not bit-identical to any cv2 interpolation, so we keep PIL to feed the network
exactly what it was trained on.

Frames are read straight out of the full match video by seeking once to the
segment start and then decoding sequentially — the same trick
``modules.common.frame_composite`` uses, so no segment mp4s need to be cut.
"""

from __future__ import annotations

import math
from typing import Callable

import cv2
import numpy as np
import torch
from PIL import Image

from modules.shuttle_tracking.tracknet import HEIGHT, WIDTH, LoadedTrackNet

# Temporal ensembling: how the overlapping windows covering a frame are combined.
# ``nonoverlap`` steps a full window at a time, so every frame is predicted once
# — far cheaper than the sliding-by-1 modes and the project default.
EVAL_MODES = ("nonoverlap", "weight", "average")

ProgressFn = Callable[[float], None]


# --------------------------------------------------------------------------- #
# Frame reading
# --------------------------------------------------------------------------- #


def read_segment_frames(
    video_path: str,
    start_frame: int,
    end_frame: int,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Decode ``[start_frame, end_frame]`` as 512x288 RGB frames.

    Returns ``(frames, (orig_width, orig_height))``. ``frames`` is
    ``(T, 288, 512, 3)`` uint8 RGB; the original size is reported separately
    because every coordinate we emit downstream is in source pixels.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {video_path}")

    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    start = max(0, int(start_frame))
    end = int(end_frame) if total <= 0 else min(int(end_frame), total - 1)
    if end < start:
        cap.release()
        raise ValueError(f"empty frame range [{start_frame}, {end_frame}]")

    if start > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)

    # Filled in place rather than collected into a list and stacked: stacking copies,
    # so the list form holds two full frame buffers at once — hundreds of MB of pure
    # transient, and the largest single term in this stage's peak memory.
    wanted = end - start + 1
    frames = np.empty((wanted, HEIGHT, WIDTH, 3), dtype=np.uint8)
    count = 0
    for _ in range(wanted):
        ok, frame = cap.read()
        if not ok:
            break
        rgb = frame[:, :, ::-1]  # BGR -> RGB
        frames[count] = np.asarray(Image.fromarray(rgb).resize((WIDTH, HEIGHT)))
        count += 1
    cap.release()

    if count == 0:
        raise ValueError(f"no frames decoded from [{start_frame}, {end_frame}] of {video_path}")
    return frames[:count], (orig_w, orig_h)


# --------------------------------------------------------------------------- #
# Model input assembly
# --------------------------------------------------------------------------- #


#: Frames sampled to build the background median. ``np.median`` copies whatever it
#: is given, so medianing every frame of a segment doubles the stage's peak memory
#: for no benefit: the background is static, and a uniform sample of this many
#: frames pins it just as well.
MEDIAN_SAMPLES = 120


def compute_median(frames: np.ndarray, bg_mode: str) -> np.ndarray | None:
    """Background image for ``bg_mode="concat"``: (3, 288, 512) uint8, or None.

    The reference pipeline medians the source-resolution frames and then resizes;
    we median the already-resized frames. For a near-static background the two
    agree closely, and it is what lets a segment stay in memory.
    """
    if not bg_mode:
        return None
    if bg_mode != "concat":
        raise NotImplementedError(
            f'bg_mode "{bg_mode}" is not supported: it differences frames at source '
            "resolution, which this stage never holds. Use a concat-mode checkpoint."
        )
    if len(frames) > MEDIAN_SAMPLES:
        frames = frames[np.linspace(0, len(frames) - 1, MEDIAN_SAMPLES, dtype=int)]
    median = np.median(frames, axis=0).astype(np.uint8)  # (288, 512, 3)
    return np.moveaxis(median, -1, 0)  # (3, 288, 512)


def build_input_sequence(
    frames: np.ndarray,
    indices: list[int],
    median: np.ndarray | None,
    bg_mode: str,
) -> np.ndarray:
    """Assemble one model input ``(C, 288, 512)`` float32 from a frame window."""
    seq = np.concatenate(
        [np.moveaxis(frames[i], -1, 0) for i in indices], axis=0
    )  # (3L, H, W)
    if bg_mode == "concat":
        if median is None:
            raise ValueError('bg_mode "concat" requires a median image')
        seq = np.concatenate((median, seq), axis=0)  # ((L+1)*3, H, W)
    elif bg_mode:
        raise NotImplementedError(f'bg_mode "{bg_mode}" is not supported')
    return (seq / 255.0).astype(np.float32)


def nonoverlap_windows(num_frames: int, seq_len: int) -> list[list[int]]:
    """Frame windows that step a whole ``seq_len`` at a time, covering every frame.

    The reference implementation drops the incomplete tail window, leaving the
    last few frames of a clip with no prediction at all. We instead anchor a final
    window at ``T - seq_len``: the tail is covered, at the cost of re-predicting a
    few frames that an earlier window already covered (identical model, so the
    overwrite is harmless). Segments shorter than one window repeat their last
    frame to fill it.
    """
    if num_frames <= seq_len:
        return [[min(i, num_frames - 1) for i in range(seq_len)]]

    starts = list(range(0, num_frames - seq_len + 1, seq_len))
    if starts[-1] + seq_len < num_frames:
        starts.append(num_frames - seq_len)
    return [list(range(s, s + seq_len)) for s in starts]


def sliding_windows(num_frames: int, seq_len: int) -> list[list[int]]:
    """Frame windows stepping one frame at a time (the ensemble eval modes)."""
    if num_frames <= seq_len:
        return nonoverlap_windows(num_frames, seq_len)
    return [list(range(i, i + seq_len)) for i in range(num_frames - seq_len + 1)]


def ensemble_weight(seq_len: int, eval_mode: str) -> "torch.Tensor":
    """Weights combining the ``seq_len`` overlapping predictions of one frame.

    ``average`` weights them equally; ``weight`` favours predictions made from the
    middle of a window, where the frame has temporal context on both sides.
    """
    if eval_mode == "average":
        return torch.ones(seq_len) / seq_len
    if eval_mode == "weight":
        w = torch.ones(seq_len)
        for i in range(math.ceil(seq_len / 2)):
            w[i] = i + 1
            w[seq_len - i - 1] = i + 1
        return w / w.sum()
    raise ValueError(f"invalid ensemble mode: {eval_mode}")


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #


def _quantize(heatmap: "np.ndarray | torch.Tensor") -> np.ndarray:
    """Raw sigmoid confidence in [0, 1] -> uint8, the on-disk heatmap dtype."""
    arr = heatmap.numpy() if isinstance(heatmap, torch.Tensor) else heatmap
    return np.clip(arr * 255.0, 0, 255).astype(np.uint8)


def _iter_batches(frames, windows, median, bg_mode, batch_size):
    for s in range(0, len(windows), batch_size):
        chunk = windows[s : s + batch_size]
        x = np.stack([build_input_sequence(frames, w, median, bg_mode) for w in chunk])
        yield chunk, torch.from_numpy(x)


def forward(net: LoadedTrackNet, x: "torch.Tensor") -> "torch.Tensor":
    """One batch through TrackNet, splitting it in half rather than dying on OOM.

    VRAM per sample is substantial (~0.65 GB at 512x288), so a batch size tuned on a
    roomy GPU will exhaust a smaller one. Halving on ``OutOfMemoryError`` and
    retrying means the stage adapts to whatever card it lands on instead of
    crashing — worst case it degrades to one sample at a time.
    """
    try:
        with torch.no_grad():
            return net.model(x.to(net.device)).detach().cpu()
    except torch.cuda.OutOfMemoryError:
        if len(x) == 1:
            raise RuntimeError(
                "CUDA ran out of memory on a single frame window. This GPU is too "
                "small for TrackNet at 512x288; re-run with --device cpu (slower, but "
                "it will finish)."
            ) from None
        torch.cuda.empty_cache()
        half = len(x) // 2
        return torch.cat([forward(net, x[:half]), forward(net, x[half:])])


def infer_heatmaps(
    frames: np.ndarray,
    net: LoadedTrackNet,
    eval_mode: str = "nonoverlap",
    batch_size: int = 8,
    on_progress: ProgressFn | None = None,
) -> np.ndarray:
    """Predict a heatmap for every frame: ``(T, 288, 512)`` uint8.

    Values are the raw sigmoid confidence scaled to 0-255 and are **not**
    thresholded — the trajectory extractors downstream need the full response.
    """
    if eval_mode not in EVAL_MODES:
        raise ValueError(f"invalid eval_mode {eval_mode!r}; expected one of {EVAL_MODES}")

    if eval_mode == "nonoverlap":
        return _infer_nonoverlap(frames, net, batch_size, on_progress)
    return _infer_ensemble(frames, net, eval_mode, batch_size, on_progress)


def _infer_nonoverlap(
    frames: np.ndarray,
    net: LoadedTrackNet,
    batch_size: int,
    on_progress: ProgressFn | None,
) -> np.ndarray:
    num_frames = len(frames)
    median = compute_median(frames, net.bg_mode)
    windows = nonoverlap_windows(num_frames, net.seq_len)
    out = np.zeros((num_frames, HEIGHT, WIDTH), dtype=np.uint8)

    total_batches = math.ceil(len(windows) / batch_size)
    for b, (chunk, x) in enumerate(
        _iter_batches(frames, windows, median, net.bg_mode, batch_size), start=1
    ):
        y = forward(net, x)  # (B, L, H, W)
        for i, window in enumerate(chunk):
            for f, frame_idx in enumerate(window):
                if frame_idx < num_frames:
                    out[frame_idx] = _quantize(y[i][f])
        if on_progress:
            on_progress(b / total_batches)
    return out


def _infer_ensemble(
    frames: np.ndarray,
    net: LoadedTrackNet,
    eval_mode: str,
    batch_size: int,
    on_progress: ProgressFn | None,
) -> np.ndarray:
    """Sliding-by-one inference: every frame is predicted ``seq_len`` times, from a
    different position in the window each time, and the predictions are combined.

    Costs ``seq_len`` times the compute of ``nonoverlap`` for a modest accuracy
    gain, which is why it is not the default. Ported from the TrackNetV3 reference
    ``predict.py``: a rolling buffer holds the last ``seq_len - 1`` batches of
    predictions so the diagonal belonging to one frame can be gathered.
    """
    num_frames = len(frames)
    seq_len = net.seq_len
    median = compute_median(frames, net.bg_mode)
    windows = sliding_windows(num_frames, seq_len)
    out = np.zeros((num_frames, HEIGHT, WIDTH), dtype=np.uint8)

    if num_frames <= seq_len:  # too short to ensemble; one window covers it all
        return _infer_nonoverlap(frames, net, batch_size, on_progress)

    num_sample = len(windows)
    buffer_size = seq_len - 1
    batch_i = torch.arange(seq_len)
    frame_i = torch.arange(seq_len - 1, -1, -1)
    buffer = torch.zeros((buffer_size, seq_len, HEIGHT, WIDTH), dtype=torch.float32)
    weight = ensemble_weight(seq_len, eval_mode)
    sample_count = 0

    total_batches = math.ceil(num_sample / batch_size)
    for b, (chunk, x) in enumerate(
        _iter_batches(frames, windows, median, net.bg_mode, batch_size), start=1
    ):
        y = forward(net, x)  # (B, L, H, W)
        buffer = torch.cat((buffer, y), dim=0)

        for i, window in enumerate(chunk):
            if sample_count < buffer_size:
                # Buffer not yet full: average whatever predictions exist so far.
                ens = buffer[batch_i + i, frame_i].sum(0) / (sample_count + 1)
            else:
                ens = (buffer[batch_i + i, frame_i] * weight[:, None, None]).sum(0)
            out[window[0]] = _quantize(ens)
            sample_count += 1

            if sample_count == num_sample:
                # Final window: flush its trailing frames, which no later window
                # will ever cover.
                buffer = torch.cat(
                    (buffer, torch.zeros((buffer_size, seq_len, HEIGHT, WIDTH))), dim=0
                )
                last = chunk[-1]
                for f in range(1, seq_len):
                    ens = buffer[batch_i + i + f, frame_i].sum(0) / (seq_len - f)
                    out[last[f]] = _quantize(ens)

        buffer = buffer[-buffer_size:]
        if on_progress:
            on_progress(b / total_batches)
    return out
