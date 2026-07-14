"""The pose cache: ``cache/pose/seg{NNNN}.npz`` plus a ``meta.json``.

Same bargain as ``shuttle_tracking``'s heatmap cache, for the same reasons. The GPU
pass (detect + pose every frame of every rally) is the expensive part; picking the two
players out of the result is milliseconds. Caching every *candidate* — everyone who
could conceivably be on the court, not just the two who were chosen — means the
selection heuristics and their margins can be retuned and re-run against an existing
cache without touching the GPU, and an interrupted run resumes at the segment it
stopped on.

Detections are ragged: a frame holds however many people were visible, from zero to a
dozen. Rather than pay for an object array, each segment's frames are concatenated and
a per-frame ``counts`` row says how to cut them apart again — so everything stays a
dense numeric array that npz can compress.

``meta.json`` pins what the detections are a function of (both models, the pose input
size, the source video, the segment boundaries). Any mismatch rebuilds the cache, so
swapping to a bigger RTMPose or re-cutting the segments can never leave stale
skeletons behind.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from modules.contracts import cache_path
from modules.pose.estimator import DET_MODEL, NUM_KEYPOINTS, POSE_MODELS

CACHE_SUBDIR = "pose"
META_FILENAME = "meta.json"


def pose_dir(match_path: str | Path) -> Path:
    """``matches/{match}/cache/pose`` — where the per-segment npz files live."""
    return Path(cache_path(match_path)) / CACHE_SUBDIR


def segment_file(match_path: str | Path, segment_index: int) -> Path:
    return pose_dir(match_path) / f"seg{segment_index:04d}.npz"


def build_meta(
    *,
    pose_mode: str,
    person_min_area: float,
    candidate_margins: tuple[float, float],
    video: str | Path,
    segments: list[dict],
) -> dict:
    """Describe the inputs a cached detection set is a function of.

    The models are identified by their URLs: they are immutable published artifacts, so
    the URL pins the weights as firmly as a hash would, without downloading anything to
    decide whether the cache is valid.

    ``candidate_margins`` is here because the pre-filter runs *before* pose and so
    decides who is in the cache at all. This is what keeps re-selection honest: widening
    the selection margins past the band that was cached would otherwise silently search
    a region containing people who were never posed, and instead rebuilds the cache.
    Tuning *within* the cached band — the usual case — still costs nothing.
    """
    pose_url, pose_input = POSE_MODELS[pose_mode]
    return {
        "pose_model": pose_url,
        "pose_input": list(pose_input),
        "det_model": DET_MODEL,
        # Both filters happen before pose estimation, so they change what is cached.
        "person_min_area": float(person_min_area),
        "candidate_margins": [float(m) for m in candidate_margins],
        "video": Path(video).name,
        "segments": [[int(s["start_frame"]), int(s["end_frame"])] for s in segments],
    }


def read_meta(match_path: str | Path) -> dict | None:
    path = pose_dir(match_path) / META_FILENAME
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None  # unreadable meta == no usable cache


def write_meta(match_path: str | Path, meta: dict) -> None:
    directory = pose_dir(match_path)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / META_FILENAME).open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def prepare(match_path: str | Path, meta: dict, force: bool = False) -> bool:
    """Ready the cache dir for ``meta``; wipe it if what is there does not match.

    Returns True when an existing, matching cache was kept (its npz files are
    reusable), False when the directory was (re)created empty.
    """
    directory = pose_dir(match_path)
    if not force and read_meta(match_path) == meta:
        return True

    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_meta(match_path, meta)
    return False


def save_segment(path: str | Path, detections: list[dict]) -> None:
    """Write one segment's per-frame detections, concatenated with a counts index."""
    counts = np.asarray([len(d["bboxes"]) for d in detections], dtype=np.int32)

    def stack(key: str, shape: tuple[int, ...]) -> np.ndarray:
        parts = [d[key] for d in detections if len(d[key])]
        if not parts:
            return np.zeros((0, *shape), np.float32)
        return np.concatenate(parts, axis=0).astype(np.float32)

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        target,
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
