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
and rebuilds instead.

The homography that *positions* that band is deliberately not part of the key, because
keying on it would throw away a 16-28 minute GPU pass every time somebody nudged a
corner. It is recorded as a note instead — the whole matrix, not a hash of it — so the
stage can ask the only question that matters and answer it exactly: does the repaired
court let a player stand somewhere the old band never posed? See :func:`court_note`,
:func:`court_status` and :func:`modules.pose.select.band_escape`.

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


def court_note(image_to_court) -> list[list[float]]:
    """The homography as the manifest records it.

    Stored whole rather than hashed, because the question a moved court raises is not
    *whether* it moved but whether the band moved off the people already posed — and
    only the matrix itself can answer that (:func:`modules.pose.select.band_escape`).

    Scaled to a largest element of 1 before rounding, which makes the rounding relative
    and the note canonical at once. A homography is only defined up to a factor, so two
    that differ by one describe the same court and must record as the same court; and
    the image -> court direction has elements around 1e-2, where rounding at a fixed
    number of decimals would quietly keep three significant figures of a matrix this
    then hands back to be measured with.
    """
    matrix = np.asarray(image_to_court, dtype=np.float64)
    scale = float(np.abs(matrix).max())
    if scale > 0:
        matrix = matrix / scale
    return [[round(float(v), 9) for v in row] for row in matrix]


def court_fingerprint(image_to_court) -> str:
    """The pre-matrix note: a short hash of the homography, and nothing to compute from.

    Frozen, deliberately, including the blunt rounding :func:`court_note` moved away
    from — its only job is to let a manifest written before the note held a matrix
    still recognise its own court, so that an unmoved one upgrades in place instead of
    reading as a court nobody can measure. Redefining it would strand every cache it
    exists for.
    """
    rounded = [[round(float(v), 6) for v in row] for row in np.asarray(image_to_court)]
    return hashlib.sha256(canonical(rounded).encode("utf-8")).hexdigest()[:16]


def open_cache(match_path: str | Path, params: dict, court=None) -> SegmentCache:
    """Open the cache. ``court`` is recorded as a *note*, not as part of the key.

    The homography does decide who passed the candidate band, so strictly it belongs in
    the key — but putting it there means every re-fit of the court throws away the whole
    GPU pass, which is the single most expensive thing this project does (measured:
    16-28 minutes a match). The band is deliberately far wider than the selection it
    feeds, so a nudged court usually does not change who is in it.

    "Usually" is not good enough on its own, which is why the note holds the whole
    matrix: :func:`court_status` and ``band_escape`` turn it into the exact question —
    does the new band reach anywhere the old one did not? — so a nudge costs nothing
    and a genuinely repositioned court rebuilds itself without being asked.
    """
    return SegmentCache(
        pose_dir(match_path), params, suffix=".npz",
        notes={"court": court} if court is not None else None,
    )


def cached_court(cache: SegmentCache) -> np.ndarray | None:
    """The homography the cached entries were filtered against, if it is recoverable.

    ``None`` for a cache with no manifest, and for one written before the note held the
    matrix — a fingerprint says a court moved but not where to.
    """
    manifest = cache.read_manifest() or {}
    court = (manifest.get("notes") or {}).get("court")
    if not isinstance(court, list):
        return None            # absent, or a pre-matrix fingerprint
    try:
        matrix = np.asarray(court, dtype=np.float64)
    except (TypeError, ValueError):
        return None            # a manifest nobody here wrote
    return matrix if matrix.shape == (3, 3) else None


def court_status(cache: SegmentCache, image_to_court) -> str:
    """How the court now compares with the one the cache was built under.

    * ``"new"`` — nothing cached yet; nothing to compare.
    * ``"same"`` — the court has not moved, so the cache covers it by construction.
    * ``"moved"`` — it moved, and the old matrix is on hand to measure by how much.
    * ``"unknown"`` — it moved, but the manifest predates :func:`court_note` and only
      kept a fingerprint. Unanswerable rather than bad; see the caller.
    """
    manifest = cache.read_manifest()
    if manifest is None:
        return "new"
    previous = (manifest.get("notes") or {}).get("court")
    if previous is None:
        return "new"
    if previous == court_note(image_to_court):
        return "same"
    if isinstance(previous, str):
        # A pre-matrix note. It can still settle the easy half of the question.
        return "same" if previous == court_fingerprint(image_to_court) else "unknown"
    # "moved" is a promise that the old matrix can be measured against; anything the
    # manifest holds that is not one is unreadable rather than different.
    return "moved" if cached_court(cache) is not None else "unknown"


def earned_notes(cache: SegmentCache, plan, status: str) -> dict | None:
    """The notes this run may write. ``None`` means the cache's own (current) ones.

    ``court`` names the court every *surviving* entry was filtered against, so a run
    that reused even one old entry has not earned a new value for it. Without that
    rule the note advances on a pass that computed nothing, the cache stops being able
    to say what built it, and a sequence of nudges — each within the band before it —
    drifts the entries out from under a note that keeps insisting it is current.

    A court that did not move is the exception: rewriting the note is then truthful,
    and it is what upgrades a pre-matrix fingerprint into something measurable.
    """
    if status == "same" or not plan.hits:
        return None
    return (cache.read_manifest() or {}).get("notes")


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
