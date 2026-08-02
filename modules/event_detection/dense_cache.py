"""The dense-scan cache: one file per rally segment under ``cache/dense_scan/``.

Same shape, and the same reasoning, as ``shuttle_tracking.heatmap_cache``: BST's
frame-by-frame probabilities are derived media — expensive (one forward pass per frame of
every rally), cheap to reproduce, and read by nobody outside this stage — so they belong
under ``cache/``, not in a stage contract. One file per segment means an interrupted scan
resumes, and every input the numbers are a function of is hashed into the file's name, so
re-cutting one rally rebuilds that rally and nothing else.

**The full 25-class probability block is stored, not the argmax.** Every threshold in this
stage — side margins, lock-region confidence, onset gates — is a threshold on these
numbers, and they are exactly what wants tuning. Keeping the probabilities means every
tuning pass after the first costs no GPU at all, which is the entire reason the split
exists. It is also nearly free: a 25 fps match of ~30k rally frames is about 3 MB, against
the several GB of heatmaps upstream.

**The scan eats shuttle.json and pose.json**, so those are keyed in too, per segment,
via :func:`upstream_digests`. Re-running ``shuttle_tracking`` with a different threshold
has to rebuild the scan or BST would be reading one trajectory while the detector reads
another — but only for the segments whose numbers actually moved.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from modules.artifacts import read_records
from modules.common.segment_cache import SegmentCache, atomic_savez, canonical
from modules.contracts import PIPELINE, SegmentIndex, artifact_path, cache_path

CACHE_SUBDIR = "dense_scan"


def dense_dir(match_path: str | Path) -> Path:
    """``matches/{match}/cache/dense_scan`` — where the per-segment files live."""
    return Path(cache_path(match_path)) / CACHE_SUBDIR


def checkpoint_fingerprint(path: str | Path) -> str:
    """Short content hash of the BST weight, so swapping it invalidates the cache."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def build_params(
    *,
    checkpoint: str | Path,
    half: int,
    shuttle_method: str,
) -> dict:
    """The global inputs a cached scan is a function of.

    ``half`` rather than fps: the half-second window half-width is the only way fps reaches
    the model, and recording the derived value means a video probed at 30.000007 fps does
    not invalidate a cache built at 30.0.

    ``shuttle_method`` selects *which* trajectory is read; the trajectory's own contents
    are keyed per segment by :func:`upstream_digests`.
    """
    return {
        "checkpoint": Path(checkpoint).name,
        "checkpoint_sha": checkpoint_fingerprint(checkpoint),
        "half": int(half),
        "shuttle_method": shuttle_method,
    }


def open_cache(match_path: str | Path, params: dict) -> SegmentCache:
    return SegmentCache(dense_dir(match_path), params, suffix=".npz")


def upstream_digests(
    match_path: str | Path,
    segments: list[dict],
    shuttle_method: str,
) -> list[str]:
    """One fingerprint per segment of the shuttle and pose data BST will read.

    ``segment_index`` is deliberately excluded from what is hashed. It is a *position*,
    and it shifts for every later rally the moment one is inserted or removed upstream —
    hashing it would invalidate the whole match over a change to one rally, which is the
    exact failure this cache exists to avoid. Absolute frames identify the data instead.
    """
    buckets: list[list[str]] = [[] for _ in segments]
    index = SegmentIndex(segments)

    def place(frame: int, payload: list) -> None:
        located = index.locate(frame)
        if located is not None:
            buckets[located[0]].append(canonical(payload))

    for point in read_records(
        PIPELINE["shuttle_tracking"], artifact_path(match_path, "shuttle_tracking")
    ):
        if point.get("method") != shuttle_method:
            continue
        place(
            int(point["frame"]),
            [point["frame"], point.get("x"), point.get("y"), point.get("visible")],
        )

    for record in read_records(PIPELINE["pose"], artifact_path(match_path, "pose")):
        place(
            int(record["frame"]),
            [record["frame"], record.get("player"), record.get("keypoints"), record.get("bbox")],
        )

    digests = []
    for rows in buckets:
        h = hashlib.sha256()
        for row in rows:
            h.update(row.encode("utf-8"))
            h.update(b"\x00")
        digests.append(h.hexdigest()[:16])
    return digests


def save_segment(path: str | Path, probabilities: np.ndarray, start_frame: int) -> None:
    """Write one segment's ``(n_frames, 25)`` probabilities."""
    atomic_savez(
        path,
        probabilities=np.asarray(probabilities, dtype=np.float32),
        start_frame=np.asarray(start_frame, dtype=np.int64),
    )


def load_segment(path: str | Path) -> tuple[np.ndarray, int]:
    """Read back ``(probabilities, start_frame)``."""
    with np.load(path) as data:
        return data["probabilities"], int(data["start_frame"])
