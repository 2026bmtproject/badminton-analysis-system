"""The heatmap cache: one file per rally segment under ``cache/heatmaps/``.

Heatmaps are derived media, not a stage contract: expensive to compute (a GPU
pass over every rally frame), cheap to reproduce, and consumed only inside
``shuttle_tracking``. So they live under ``cache/`` per the match layout, not
under ``stages/``.

**Resume is a cache property, not a stage property.** One file per segment means an
interrupted run picks up where it stopped, and — the real payoff — a second
trajectory extractor can be developed and re-run against an existing cache
without touching the GPU.

**Heatmaps are sparsified before writing.** The raw sigmoid response carries
low-level noise across the whole frame, which compresses terribly: a 30 s rally is
130 MB dense, and a match would run to several GB. Every consumer already ignores
values below ``STORE_THRESHOLD`` (the candidate extractor's floor, and far below
the 0.5 binarization the blob extractor uses), so zeroing them discards nothing
that is read back while making the array mostly zeros — which npz compresses by
orders of magnitude. The threshold is part of the cache key: lowering a consumer's
own threshold below it rebuilds rather than silently reading truncated data.

Everything the heatmaps depend on (checkpoint, its training params, eval mode,
source video, the segment's own frame range) is hashed into each file's name by
:mod:`modules.common.segment_cache`, so re-cutting one rally invalidates that
rally and nothing else.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from modules.common.segment_cache import SegmentCache, atomic_savez
from modules.contracts import cache_path

CACHE_SUBDIR = "heatmaps"

#: Confidence (0-255) below which a heatmap pixel is stored as 0. See module docstring.
STORE_THRESHOLD = 10


def heatmap_dir(match_path: str | Path) -> Path:
    """``matches/{match}/cache/heatmaps`` — where the per-segment files live."""
    return Path(cache_path(match_path)) / CACHE_SUBDIR


def checkpoint_fingerprint(path: str | Path) -> str:
    """Short content hash of a checkpoint, so swapping weights invalidates the cache."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def build_params(
    *,
    checkpoint: str | Path,
    eval_mode: str,
    chunk_frames: int,
    video: str | Path,
) -> dict:
    """The global inputs a cached heatmap set is a function of.

    The checkpoint's own training params (``seq_len``, ``bg_mode``) are *not*
    listed: they are properties of the file, already pinned by its hash. Keeping
    them out means cache validity can be decided by hashing the checkpoint rather
    than loading it — so re-running the trackers against a complete cache never
    builds the network or touches the GPU.

    Segment boundaries are absent by design; they key the individual entries, not
    the cache as a whole.
    """
    return {
        "checkpoint": Path(checkpoint).name,
        "checkpoint_sha": checkpoint_fingerprint(checkpoint),
        "eval_mode": eval_mode,
        # A long segment is inferred in chunks, and a frame window cannot cross a
        # chunk boundary — so the chunk size shows through in the heatmaps.
        "chunk_frames": int(chunk_frames),
        "store_threshold": STORE_THRESHOLD,
        "video": Path(video).name,
    }


def open_cache(match_path: str | Path, params: dict) -> SegmentCache:
    return SegmentCache(heatmap_dir(match_path), params, suffix=".npz")


def save_segment(
    path: str | Path,
    heatmaps: np.ndarray,
    img_shape: tuple[int, int],
) -> None:
    """Sparsify and write one segment's heatmaps.

    ``heatmaps`` is ``(T, 288, 512)`` uint8; ``img_shape`` is the source video's
    ``(width, height)``, carried along because every coordinate derived from these
    heatmaps must be scaled back into source pixels.
    """
    sparse = np.where(heatmaps < STORE_THRESHOLD, 0, heatmaps)
    atomic_savez(
        path,
        heatmaps=sparse,
        img_shape=np.asarray(img_shape, dtype=np.int32),
        hm_shape=np.asarray(sparse.shape[1:][::-1], dtype=np.int32),  # (W, H)
    )


def load_segment(path: str | Path) -> tuple[np.ndarray, tuple[int, int]]:
    """Read back ``(heatmaps, (orig_width, orig_height))``."""
    with np.load(path) as data:
        heatmaps = data["heatmaps"]
        w, h = (int(v) for v in data["img_shape"])
    return heatmaps, (w, h)
