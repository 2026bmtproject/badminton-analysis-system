"""Tracker A — TrackNetV3's own trajectory repair, via InpaintNet.

Takes the baseline blob track (see ``blob.py``) and fills the frames where the
heatmap gave nothing. A 1-D U-Net over the coordinate sequence learns what a
shuttle trajectory looks like and hallucinates the missing positions from the
surrounding motion.

Not every gap should be filled: when the shuttle leaves the top of the frame on a
high clear, "missing" is the truth, and inventing a position there is worse than
admitting ignorance. ``inpaint_mask`` therefore only marks a gap for repair when
the shuttle was well inside the frame on **both** sides of it.

This is one of two trackers whose outputs both land in ``shuttle.json``;
``track_viterbi`` is the other. They are alternatives to each other, not stages of
one pipeline — ``event_detection`` reads both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from modules.shuttle_tracking.inference import ensemble_weight
from modules.shuttle_tracking.tracknet import HEIGHT, WIDTH, pick_device

#: Normalized coordinates this close to the origin mean "no position" — the
#: network's way of emitting nothing. 50 heatmap-pixels' worth of slack, in the
#: normalized units the network works in.
COOR_TH = 50 / math.sqrt(HEIGHT**2 + WIDTH**2)

#: A gap is only inpainted when the shuttle sits below this fraction of the frame
#: height on both sides of it; above that it has likely flown out of view.
TOP_MARGIN_RATIO = 0.05


class Conv1DBlock(nn.Module):
    """Conv1D + LeakyReLU."""

    def __init__(self, in_dim: int, out_dim: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.conv = nn.Conv1d(in_dim, out_dim, kernel_size=3, padding="same", bias=True)
        self.relu = nn.LeakyReLU()

    def forward(self, x):
        return self.relu(self.conv(x))


class Double1DConv(nn.Module):
    """Conv1DBlock x 2."""

    def __init__(self, in_dim: int, out_dim: int) -> None:
        super().__init__()
        self.conv_1 = Conv1DBlock(in_dim, out_dim)
        self.conv_2 = Conv1DBlock(out_dim, out_dim)

    def forward(self, x):
        return self.conv_2(self.conv_1(x))


class InpaintNet(nn.Module):
    """1-D U-Net over a coordinate sequence: (x, y, mask) -> (x, y).

    Layer names are the released checkpoint's state-dict keys — including the
    misspelled ``buttleneck``. Renaming them breaks weight loading.
    """

    def __init__(self) -> None:
        super().__init__()
        self.down_1 = Conv1DBlock(3, 32)
        self.down_2 = Conv1DBlock(32, 64)
        self.down_3 = Conv1DBlock(64, 128)
        self.buttleneck = Double1DConv(128, 256)
        self.up_1 = Conv1DBlock(384, 128)
        self.up_2 = Conv1DBlock(192, 64)
        self.up_3 = Conv1DBlock(96, 32)
        self.predictor = nn.Conv1d(32, 2, 3, padding="same")
        self.sigmoid = nn.Sigmoid()

    def forward(self, x, m):
        x = torch.cat([x, m], dim=2)  # (N, L, 3)
        x = x.permute(0, 2, 1)  # (N, 3, L)
        x1 = self.down_1(x)
        x2 = self.down_2(x1)
        x3 = self.down_3(x2)
        x = self.buttleneck(x3)
        x = torch.cat([x, x3], dim=1)
        x = self.up_1(x)
        x = torch.cat([x, x2], dim=1)
        x = self.up_2(x)
        x = torch.cat([x, x1], dim=1)
        x = self.up_3(x)
        x = self.predictor(x)
        x = self.sigmoid(x)
        return x.permute(0, 2, 1)  # (N, L, 2)


@dataclass(frozen=True)
class LoadedInpaintNet:
    model: InpaintNet
    seq_len: int
    device: "torch.device"


def load_inpaintnet(checkpoint: str | Path, device: str | None = None) -> LoadedInpaintNet:
    """Load an InpaintNet ``.pt`` in eval mode; ``seq_len`` comes from the checkpoint."""
    path = Path(checkpoint)
    if not path.is_file():
        raise FileNotFoundError(
            f"InpaintNet checkpoint not found: {path}\n"
            "Download it (see README) and place it under models/."
        )
    dev = pick_device(device)
    ckpt = torch.load(path, map_location=dev, weights_only=False)
    model = InpaintNet().to(dev)
    model.load_state_dict(ckpt["model"])
    model.eval()
    return LoadedInpaintNet(model, int(ckpt["param_dict"]["seq_len"]), dev)


def inpaint_mask(
    xy: np.ndarray,
    img_height: int,
    top_margin_ratio: float = TOP_MARGIN_RATIO,
) -> np.ndarray:
    """Mark the missing frames worth repairing: ``(T,)`` of 0/1.

    Walks the runs of consecutive missing frames. A run is marked only when the
    shuttle was below ``top_margin_ratio`` of the frame height (i.e. comfortably
    in view) on both sides — a gap bracketed by near-the-top positions is the
    shuttle leaving the camera, and stays empty. A run at the very start of the
    segment is judged by its right-hand side alone, since it has no left one.
    """
    num_frames = len(xy)
    y = xy[:, 1]
    visible = ~np.isnan(xy[:, 0])
    threshold = img_height * top_margin_ratio
    mask = np.zeros(num_frames, dtype=int)

    i = 0
    while i < num_frames:
        if visible[i]:
            i += 1
            continue
        start = i  # first missing frame of this run
        while i < num_frames and not visible[i]:
            i += 1
        end = i  # first visible frame after the run (may be num_frames)

        if end >= num_frames:
            break  # trailing run: no right-hand anchor to judge it by
        if start == 0:
            if y[end] > threshold:
                mask[:end] = 1
        elif y[start - 1] > threshold and y[end] > threshold:
            mask[start:end] = 1

    return mask


def _windows(num_frames: int, seq_len: int, step: int) -> np.ndarray:
    """Frame-index windows ``(N, seq_len)``; the tail repeats the last frame to fill."""
    if num_frames <= seq_len:
        pad = list(range(num_frames)) + [num_frames - 1] * (seq_len - num_frames)
        return np.array([pad], dtype=np.int64)

    windows = []
    for i in range(0, num_frames, step):
        window = list(range(i, min(i + seq_len, num_frames)))
        if len(window) < seq_len:
            if step != seq_len:
                break  # sliding mode: an incomplete window adds nothing
            window += [num_frames - 1] * (seq_len - len(window))
        windows.append(window)
    return np.array(windows, dtype=np.int64)


def _clip_to_missing(coor: "torch.Tensor") -> "torch.Tensor":
    """Snap near-origin outputs to exactly zero — the network's "no position"."""
    origin = (coor[..., 0] < COOR_TH) & (coor[..., 1] < COOR_TH)
    coor[origin] = 0.0
    return coor


def track(
    xy_base: np.ndarray,
    conf_base: np.ndarray,
    img_shape: tuple[int, int],
    net: LoadedInpaintNet,
    eval_mode: str = "weight",
    batch_size: int = 16,
    top_margin_ratio: float = TOP_MARGIN_RATIO,
) -> tuple[np.ndarray, np.ndarray]:
    """Repair the baseline track. Returns ``(xy (T, 2) with NaN, conf (T,))``.

    Inpainted positions carry confidence 0: they are inferred, not observed, and
    ``event_detection`` should be able to tell the difference.
    """
    img_w, img_h = img_shape
    num_frames = len(xy_base)
    mask = inpaint_mask(xy_base, img_h, top_margin_ratio)

    # The network works in [0, 1] and marks "no position" with the origin, so
    # missing frames go in as zeros rather than NaN.
    coor = np.nan_to_num(xy_base, nan=0.0) / np.array([img_w, img_h], dtype=float)

    step = net.seq_len if eval_mode == "nonoverlap" else 1
    windows = _windows(num_frames, net.seq_len, step)

    coor_windows = coor[windows].astype(np.float32)  # (N, L, 2)
    mask_windows = mask[windows].astype(np.float32)[..., None]  # (N, L, 1)

    if eval_mode == "nonoverlap":
        out_norm = _run_nonoverlap(net, windows, coor_windows, mask_windows, batch_size, num_frames)
    else:
        out_norm = _run_ensemble(
            net, windows, coor_windows, mask_windows, batch_size, num_frames, eval_mode
        )

    xy = out_norm * np.array([img_w, img_h], dtype=float)
    missing = (out_norm[:, 0] == 0) & (out_norm[:, 1] == 0)
    xy[missing] = np.nan

    conf = np.where(mask == 1, 0.0, conf_base)  # repaired frames are not observations
    conf[missing] = 0.0
    return xy, conf


def _infer(net: LoadedInpaintNet, coor: np.ndarray, mask: np.ndarray) -> "torch.Tensor":
    """One batch through InpaintNet, keeping the observed coordinates untouched."""
    coor_t = torch.from_numpy(coor).float()
    mask_t = torch.from_numpy(mask).float()
    with torch.no_grad():
        pred = net.model(coor_t.to(net.device), mask_t.to(net.device)).detach().cpu()
    # Only masked positions take the network's output; observations pass through.
    return _clip_to_missing(pred * mask_t + coor_t * (1 - mask_t))


def _run_nonoverlap(net, windows, coor_windows, mask_windows, batch_size, num_frames):
    out = np.zeros((num_frames, 2), dtype=float)
    for s in range(0, len(windows), batch_size):
        pred = _infer(net, coor_windows[s : s + batch_size], mask_windows[s : s + batch_size])
        for i, window in enumerate(windows[s : s + batch_size]):
            out[window] = pred[i].numpy()
    return out


def _run_ensemble(net, windows, coor_windows, mask_windows, batch_size, num_frames, eval_mode):
    """Sliding-by-one: average the ``seq_len`` predictions each frame receives.

    Same rolling-buffer scheme as ``inference._infer_ensemble``, over coordinates
    instead of heatmaps.
    """
    seq_len = net.seq_len
    num_sample = len(windows)
    buffer_size = seq_len - 1
    batch_i = torch.arange(seq_len)
    frame_i = torch.arange(seq_len - 1, -1, -1)
    buffer = torch.zeros((buffer_size, seq_len, 2), dtype=torch.float32)
    weight = ensemble_weight(seq_len, eval_mode)

    out = np.zeros((num_frames, 2), dtype=float)
    sample_count = 0

    for s in range(0, num_sample, batch_size):
        chunk = windows[s : s + batch_size]
        pred = _infer(net, coor_windows[s : s + batch_size], mask_windows[s : s + batch_size])
        buffer = torch.cat((buffer, pred), dim=0)

        for i, window in enumerate(chunk):
            if sample_count < buffer_size:
                ens = buffer[batch_i + i, frame_i].sum(0) / (sample_count + 1)
            else:
                ens = (buffer[batch_i + i, frame_i] * weight[:, None]).sum(0)
            out[window[0]] = _clip_to_missing(ens.view(1, 2))[0].numpy()
            sample_count += 1

            if sample_count == num_sample:
                # Flush the final window's trailing frames; nothing else covers them.
                buffer = torch.cat((buffer, torch.zeros((buffer_size, seq_len, 2))), dim=0)
                for f in range(1, seq_len):
                    ens = buffer[batch_i + i + f, frame_i].sum(0) / (seq_len - f)
                    out[chunk[-1][f]] = _clip_to_missing(ens.view(1, 2))[0].numpy()

        buffer = buffer[-buffer_size:]
    return out
