"""TrackNetV3's heatmap network: the architecture plus checkpoint loading.

Copied from the TrackNetV3 reference implementation so the weights load into an
identically-shaped module. Do not "clean up" the layer names — they are the keys
in the released ``.pt`` state dict.

The network takes a short sequence of frames at a fixed 512x288 and emits one
confidence heatmap per input frame (sigmoid, so values are in [0, 1]).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn

# Fixed network input resolution. The released checkpoint was trained at this
# size; changing it invalidates the weights.
HEIGHT = 288
WIDTH = 512


class Conv2DBlock(nn.Module):
    """Conv2D + BN + ReLU."""

    def __init__(self, in_dim: int, out_dim: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.conv = nn.Conv2d(in_dim, out_dim, kernel_size=3, padding="same", bias=False)
        self.bn = nn.BatchNorm2d(out_dim)
        self.relu = nn.ReLU()

    def forward(self, x):
        return self.relu(self.bn(self.conv(x)))


class Double2DConv(nn.Module):
    """Conv2DBlock x 2."""

    def __init__(self, in_dim: int, out_dim: int) -> None:
        super().__init__()
        self.conv_1 = Conv2DBlock(in_dim, out_dim)
        self.conv_2 = Conv2DBlock(out_dim, out_dim)

    def forward(self, x):
        return self.conv_2(self.conv_1(x))


class Triple2DConv(nn.Module):
    """Conv2DBlock x 3."""

    def __init__(self, in_dim: int, out_dim: int) -> None:
        super().__init__()
        self.conv_1 = Conv2DBlock(in_dim, out_dim)
        self.conv_2 = Conv2DBlock(out_dim, out_dim)
        self.conv_3 = Conv2DBlock(out_dim, out_dim)

    def forward(self, x):
        return self.conv_3(self.conv_2(self.conv_1(x)))


class TrackNet(nn.Module):
    """U-Net style encoder/decoder: (B, C, 288, 512) -> (B, seq_len, 288, 512)."""

    def __init__(self, in_dim: int, out_dim: int) -> None:
        super().__init__()
        self.down_block_1 = Double2DConv(in_dim, 64)
        self.down_block_2 = Double2DConv(64, 128)
        self.down_block_3 = Triple2DConv(128, 256)
        self.bottleneck = Triple2DConv(256, 512)
        self.up_block_1 = Triple2DConv(768, 256)
        self.up_block_2 = Double2DConv(384, 128)
        self.up_block_3 = Double2DConv(192, 64)
        self.predictor = nn.Conv2d(64, out_dim, (1, 1))
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x1 = self.down_block_1(x)
        x = nn.MaxPool2d((2, 2), stride=(2, 2))(x1)
        x2 = self.down_block_2(x)
        x = nn.MaxPool2d((2, 2), stride=(2, 2))(x2)
        x3 = self.down_block_3(x)
        x = nn.MaxPool2d((2, 2), stride=(2, 2))(x3)
        x = self.bottleneck(x)
        x = torch.cat([nn.Upsample(scale_factor=2)(x), x3], dim=1)
        x = self.up_block_1(x)
        x = torch.cat([nn.Upsample(scale_factor=2)(x), x2], dim=1)
        x = self.up_block_2(x)
        x = torch.cat([nn.Upsample(scale_factor=2)(x), x1], dim=1)
        x = self.up_block_3(x)
        x = self.predictor(x)
        return self.sigmoid(x)


def input_channels(seq_len: int, bg_mode: str) -> int:
    """Input channel count implied by ``bg_mode`` (must match the checkpoint)."""
    if bg_mode == "subtract":
        return seq_len
    if bg_mode == "subtract_concat":
        return seq_len * 4
    if bg_mode == "concat":
        return (seq_len + 1) * 3
    return seq_len * 3


@dataclass(frozen=True)
class LoadedTrackNet:
    """A ready-to-run TrackNet plus the two training params inference needs."""

    model: TrackNet
    seq_len: int
    bg_mode: str
    device: "torch.device"


def pick_device(device: str | None = None) -> "torch.device":
    """CUDA when available (and not overridden), else CPU."""
    if device:
        return torch.device(device)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def free_vram_gb(device: "torch.device") -> float:
    """Free VRAM on ``device`` in GB, or 0.0 when it is not a CUDA device."""
    if device.type != "cuda":
        return 0.0
    free, _total = torch.cuda.mem_get_info(device)
    return free / 1e9


#: Measured peak VRAM per sample in a TrackNet batch (RTX 3060 Ti, 512x288 input):
#: batch 1 -> 0.68 GB, 4 -> 2.48 GB, 16 -> 5.15 GB. ~0.6 GB per extra sample on top
#: of a fixed ~0.7 GB for the model and workspace.
VRAM_PER_SAMPLE_GB = 0.65
VRAM_OVERHEAD_GB = 1.0  # model + allocator headroom we refuse to spend on batch size
MAX_AUTO_BATCH = 8


def auto_batch_size(device: "torch.device") -> int:
    """Largest batch this GPU's free VRAM can be expected to hold.

    Guessing high is not dangerous — the forward pass halves a batch that OOMs — but
    a good first guess avoids that thrash. On CPU the batch size barely matters, so
    keep it small and let RAM stay flat.
    """
    if device.type != "cuda":
        return 2
    budget = free_vram_gb(device) - VRAM_OVERHEAD_GB
    return max(1, min(MAX_AUTO_BATCH, int(budget / VRAM_PER_SAMPLE_GB)))


def describe_device(device: "torch.device") -> str:
    """One line naming the device, with its free VRAM when there is a GPU."""
    if device.type != "cuda":
        return f"{device} (no CUDA GPU detected)"
    index = device.index or 0
    name = torch.cuda.get_device_name(index)
    free, total = torch.cuda.mem_get_info(device)
    return f"{device} ({name}, {free / 1e9:.1f} of {total / 1e9:.1f} GB VRAM free)"


def load_tracknet(checkpoint: str | Path, device: str | None = None) -> LoadedTrackNet:
    """Load a TrackNet ``.pt`` and build the matching network in eval mode.

    ``seq_len`` and ``bg_mode`` come from the checkpoint's own ``param_dict``
    rather than from our config — they are properties of how it was trained, and
    guessing them wrong produces a silent shape mismatch.
    """
    path = Path(checkpoint)
    if not path.is_file():
        raise FileNotFoundError(
            f"TrackNet checkpoint not found: {path}\n"
            "Download it (see README) and place it under models/."
        )
    dev = pick_device(device)
    ckpt = torch.load(path, map_location=dev, weights_only=False)
    seq_len = int(ckpt["param_dict"]["seq_len"])
    bg_mode = str(ckpt["param_dict"]["bg_mode"])

    model = TrackNet(input_channels(seq_len, bg_mode), out_dim=seq_len).to(dev)
    model.load_state_dict(ckpt["model"])
    model.eval()
    return LoadedTrackNet(model=model, seq_len=seq_len, bg_mode=bg_mode, device=dev)
