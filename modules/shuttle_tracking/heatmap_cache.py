"""The heatmap cache: ``cache/heatmaps/seg{NNNN}.npz`` plus a ``meta.json``.

Heatmaps are derived media, not a stage contract: expensive to compute (a GPU
pass over every rally frame), cheap to reproduce, and consumed only inside
``shuttle_tracking``. So they live under ``cache/`` per the match layout, not
under ``stages/``.

**Resume is a cache property, not a stage property.** One npz per segment means an
interrupted run picks up where it stopped, and — the real payoff — a second
trajectory extractor can be developed and re-run against an existing cache
without touching the GPU.

**Heatmaps are sparsified before writing.** The raw sigmoid response carries
low-level noise across the whole frame, which compresses terribly: a 30 s rally is
130 MB dense, and a match would run to several GB. Every consumer already ignores
values below ``STORE_THRESHOLD`` (the candidate extractor's floor, and far below
the 0.5 binarization the blob extractor uses), so zeroing them discards nothing
that is read back while making the array mostly zeros — which npz compresses by
orders of magnitude. The threshold is recorded in ``meta.json``: lowering a
consumer's own threshold below it invalidates the cache instead of silently
reading truncated data.

``meta.json`` pins everything the heatmaps depend on (checkpoint, its training
params, eval mode, source video, the segment boundaries). Any mismatch rebuilds
the cache, so swapping weights or re-cutting segments can never leave stale
heatmaps behind.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from modules.contracts import cache_path

CACHE_SUBDIR = "heatmaps"
META_FILENAME = "meta.json"

#: Confidence (0-255) below which a heatmap pixel is stored as 0. See module docstring.
STORE_THRESHOLD = 10


def heatmap_dir(match_path: str | Path) -> Path:
    """``matches/{match}/cache/heatmaps`` — where the per-segment npz files live."""
    return Path(cache_path(match_path)) / CACHE_SUBDIR


def segment_file(match_path: str | Path, segment_index: int) -> Path:
    return heatmap_dir(match_path) / f"seg{segment_index:04d}.npz"


def checkpoint_fingerprint(path: str | Path) -> str:
    """Short content hash of a checkpoint, so swapping weights invalidates the cache."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def build_meta(
    *,
    checkpoint: str | Path,
    eval_mode: str,
    chunk_frames: int,
    video: str | Path,
    segments: list[dict],
) -> dict:
    """Describe the inputs a cached heatmap set is a function of.

    The checkpoint's own training params (``seq_len``, ``bg_mode``) are *not*
    listed: they are properties of the file, already pinned by its hash. Keeping
    them out means cache validity can be decided by hashing the checkpoint rather
    than loading it — so re-running the trackers against a complete cache never
    builds the network or touches the GPU.
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
        "segments": [[int(s["start_frame"]), int(s["end_frame"])] for s in segments],
    }


def read_meta(match_path: str | Path) -> dict | None:
    path = heatmap_dir(match_path) / META_FILENAME
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None  # unreadable meta == no usable cache


def write_meta(match_path: str | Path, meta: dict) -> None:
    d = heatmap_dir(match_path)
    d.mkdir(parents=True, exist_ok=True)
    with (d / META_FILENAME).open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def prepare(match_path: str | Path, meta: dict, force: bool = False) -> bool:
    """Ready the cache dir for ``meta``; wipe it if what is there does not match.

    Returns True when an existing, matching cache was kept (its npz files are
    reusable), False when the directory was (re)created empty.
    """
    d = heatmap_dir(match_path)
    if not force and read_meta(match_path) == meta:
        return True

    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    write_meta(match_path, meta)
    return False


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
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        p,
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
