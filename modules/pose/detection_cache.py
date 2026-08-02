"""The pose cache: one file per rally segment under ``cache/pose/``.

Same bargain as ``shuttle_tracking``'s heatmap cache, for the same reasons. The GPU
pass (detect + pose every frame of every rally) is the expensive part; picking the two
players out of the result is milliseconds.

Caching every *candidate* — everyone who could conceivably be on the court, not just the
two who were chosen — means the selection heuristics and their margins can be retuned and
re-run against an existing cache without touching the GPU, and an interrupted run resumes
at the segment it stopped on.

Only people inside the candidate band get a skeleton, because RTMPose is charged per
person and a broadcast frame is mostly crowd: the detector finds 8-23 people per frame
against the 2-4 near the court, so posing everyone costs several times as much for
skeletons nobody reads. The band is a *court* filter, which is what
:func:`build_params`'s ``candidate_margins`` records — searching wider at selection time
than what was posed would quietly scan a region containing people who have no skeleton,
and rebuilds instead. The homography itself is deliberately *not* part of the key; see
``court`` in the notes below.

Detections are ragged: a frame holds however many people were visible, from zero to a
dozen. Rather than pay for an object array, each segment's frames are concatenated and
a per-frame ``counts`` row says how to cut them apart again — so everything stays a
dense numeric array that npz can compress.

What the detections *are* a function of (both models, the pose input size, the
pre-filters, the source video, the segment's own frame range) is hashed into each
file's name by :mod:`modules.common.segment_cache`, so re-cutting one rally
invalidates that rally and nothing else.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from modules.common.segment_cache import SegmentCache, atomic_savez, canonical
from modules.contracts import cache_path
from modules.pose.estimator import DET_MODEL, NUM_KEYPOINTS, POSE_MODELS

CACHE_SUBDIR = "pose"


def pose_dir(match_path: str | Path) -> Path:
    """``matches/{match}/cache/pose`` — where the per-segment files live."""
    return Path(cache_path(match_path)) / CACHE_SUBDIR


def build_params(
    *,
    pose_mode: str,
    person_min_area: float,
    candidate_margins: tuple[float, float],
    video: str | Path,
) -> dict:
    """The global inputs a cached detection set is a function of.

    The models are identified by their URLs: they are immutable published artifacts, so
    the URL pins the weights as firmly as a hash would, without downloading anything to
    decide whether the cache is valid.

    Both filters run *before* pose estimation, so they change who is in the cache.
    ``candidate_margins`` is what keeps re-selection honest: widening the selection
    margins past the band that was cached would otherwise silently search a region
    containing people who were never posed, and instead rebuilds the cache. Tuning
    *within* the cached band — the usual case — still costs nothing.
    """
    pose_url, pose_input = POSE_MODELS[pose_mode]
    return {
        "pose_model": pose_url,
        "pose_input": list(pose_input),
        "det_model": DET_MODEL,
        "person_min_area": float(person_min_area),
        "candidate_margins": [float(m) for m in candidate_margins],
        "video": Path(video).name,
    }


def court_fingerprint(image_to_court) -> str:
    """A short hash of the homography the candidate band was measured against."""
    rounded = [[round(float(v), 6) for v in row] for row in np.asarray(image_to_court)]
    return hashlib.sha256(canonical(rounded).encode("utf-8")).hexdigest()[:16]


def open_cache(match_path: str | Path, params: dict, court: str | None = None) -> SegmentCache:
    """Open the cache. ``court`` is recorded as a *note*, not as part of the key.

    The homography does decide who passed the candidate band, so strictly it belongs in
    the key — but putting it there means every re-fit of the court throws away the whole
    GPU pass, which is the single most expensive thing this project does. The band is
    deliberately far wider than the selection it feeds, so a re-clicked court almost
    never changes who is in it. Recording it as a note lets the stage *say* the court
    moved and leave the choice (``--refresh-cache``) to the user.
    """
    return SegmentCache(
        pose_dir(match_path), params, suffix=".npz",
        notes={"court": court} if court is not None else None,
    )


def save_segment(path: str | Path, detections: list[dict]) -> None:
    """Write one segment's per-frame detections, concatenated with a counts index."""
    counts = np.asarray([len(d["bboxes"]) for d in detections], dtype=np.int32)

    def stack(key: str, shape: tuple[int, ...]) -> np.ndarray:
        parts = [d[key] for d in detections if len(d[key])]
        if not parts:
            return np.zeros((0, *shape), np.float32)
        return np.concatenate(parts, axis=0).astype(np.float32)

    atomic_savez(
        path,
        counts=counts,
        kps=stack("kps", (NUM_KEYPOINTS, 2)),
        scores=stack("scores", (NUM_KEYPOINTS,)),
        bboxes=stack("bboxes", (4,)),
    )


def load_segment(path: str | Path) -> list[dict]:
    """Read back the per-frame detections written by :func:`save_segment`."""
    with np.load(path) as data:
        counts = data["counts"]
        kps, scores, bboxes = data["kps"], data["scores"], data["bboxes"]

    offsets = np.concatenate([[0], np.cumsum(counts)])
    return [
        {
            "kps": kps[a:b],
            "scores": scores[a:b],
            "bboxes": bboxes[a:b],
        }
        for a, b in zip(offsets[:-1], offsets[1:])
    ]
