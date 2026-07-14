"""The dense-scan cache: ``cache/dense_scan/seg{NNNN}.npz`` plus a ``meta.json``.

Same shape, and the same reasoning, as ``shuttle_tracking.heatmap_cache``: BST's
frame-by-frame probabilities are derived media — expensive (one forward pass per frame of
every rally), cheap to reproduce, and read by nobody outside this stage — so they belong
under ``cache/``, not in a stage contract. One npz per segment means an interrupted scan
resumes, and ``meta.json`` pins everything the numbers are a function of, so swapping the
checkpoint or re-cutting the segments rebuilds rather than silently reusing.

**The full 25-class probability block is stored, not the argmax.** Every threshold in this
stage — side margins, lock-region confidence, onset gates — is a threshold on these
numbers, and they are exactly what wants tuning. Keeping the probabilities means every
tuning pass after the first costs no GPU at all, which is the entire reason the split
exists. It is also nearly free: a 25 fps match of ~30k rally frames is about 3 MB, against
the several GB of heatmaps upstream.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from modules.contracts import cache_path

CACHE_SUBDIR = "dense_scan"
META_FILENAME = "meta.json"


def dense_dir(match_path: str | Path) -> Path:
    """``matches/{match}/cache/dense_scan`` — where the per-segment npz files live."""
    return Path(cache_path(match_path)) / CACHE_SUBDIR


def segment_file(match_path: str | Path, segment_index: int) -> Path:
    return dense_dir(match_path) / f"seg{segment_index:04d}.npz"


def checkpoint_fingerprint(path: str | Path) -> str:
    """Short content hash of the BST weight, so swapping it invalidates the cache."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def build_meta(
    *,
    checkpoint: str | Path,
    half: int,
    shuttle_method: str,
    segments: list[dict],
) -> dict:
    """Describe the inputs a cached scan is a function of.

    ``half`` rather than fps: the half-second window half-width is the only way fps reaches
    the model, and recording the derived value means a video probed at 30.000007 fps does
    not invalidate a cache built at 30.0.

    ``shuttle_method`` is here because the scan eats the shuttle trajectory — running the
    scan against ``inpaint`` and then re-running the stage with ``--base-method viterbi``
    has to rebuild, or BST would be reading one trajectory while the detector reads another.
    """
    return {
        "checkpoint": Path(checkpoint).name,
        "checkpoint_sha": checkpoint_fingerprint(checkpoint),
        "half": int(half),
        "shuttle_method": shuttle_method,
        "segments": [[int(s["start_frame"]), int(s["end_frame"])] for s in segments],
    }


def read_meta(match_path: str | Path) -> dict | None:
    path = dense_dir(match_path) / META_FILENAME
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None  # unreadable meta == no usable cache


def write_meta(match_path: str | Path, meta: dict) -> None:
    d = dense_dir(match_path)
    d.mkdir(parents=True, exist_ok=True)
    with (d / META_FILENAME).open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def prepare(match_path: str | Path, meta: dict, force: bool = False) -> bool:
    """Ready the cache dir for ``meta``; wipe it if what is there does not match.

    True when an existing, matching cache was kept; False when the directory was
    (re)created empty.
    """
    d = dense_dir(match_path)
    if not force and read_meta(match_path) == meta:
        return True
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    write_meta(match_path, meta)
    return False


def save_segment(path: str | Path, probabilities: np.ndarray, start_frame: int) -> None:
    """Write one segment's ``(n_frames, 25)`` probabilities."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        p,
        probabilities=np.asarray(probabilities, dtype=np.float32),
        start_frame=np.asarray(start_frame, dtype=np.int64),
    )


def load_segment(path: str | Path) -> tuple[np.ndarray, int]:
    """Read back ``(probabilities, start_frame)``."""
    with np.load(path) as data:
        return data["probabilities"], int(data["start_frame"])
