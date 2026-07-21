"""Segment math plus the segments.json contract this stage produces.

The math functions (Otsu thresholding, building, merging, filtering) touch
neither video nor disk, so they are deterministic and cheap to reason about.
The I/O helpers own the frame->second derivation that turns raw
``(start_frame, end_frame)`` pairs into :class:`Segment`-shaped records; the
envelope plumbing itself is delegated to the generic ``modules.artifacts``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np

from modules.artifacts import read_artifact, write_artifact
from modules.contracts import PIPELINE, Segment

_SPEC = PIPELINE["match_segmentation"]

# The record field order, taken straight from the Segment contract so the
# writer and the dataclass can never drift apart.
SEGMENT_FIELDS = tuple(f.name for f in dataclasses.fields(Segment))


def build_segment_records(segments: list[tuple[int, int]], fps: float) -> list[dict]:
    """Turn (start_frame, end_frame) pairs into JSON-ready segment records.

    This is the stage's own derivation (frame indices -> seconds), not generic
    serialization, so it lives with the producer rather than in the shared I/O.
    """
    records: list[dict] = []
    for start_frame, end_frame in segments:
        start_sec = start_frame / fps
        end_sec = end_frame / fps
        records.append({
            "start_frame": int(start_frame),
            "end_frame": int(end_frame),
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
            "duration_sec": round(end_sec - start_sec, 3),
        })
    return records


def write_segments(path: str | Path, segments: list[tuple[int, int]], fps: float) -> None:
    """Write the segments artifact (envelope carries top-level ``fps``)."""
    write_artifact(
        _SPEC,
        build_segment_records(segments, fps),
        path,
        extra={"fps": float(fps)},
    )


def round_to_int(value: float) -> int:
    return int(np.rint(value))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def load_excluded_frames(json_path: str) -> set[int]:
    """Read a previously written segments JSON and return every covered frame.

    Used by "exclude & rerun" mode: if the user is unhappy with the segments
    picked last time, that JSON can be used as an exclusion list so the search
    skips those frames and looks for stable segments among the rest. Each
    record must carry start_frame / end_frame (this stage's own output).
    """
    data = read_artifact(_SPEC, json_path)

    excluded: set[int] = set()
    for record in data["segments"]:
        try:
            start_frame = int(record["start_frame"])
            end_frame = int(record["end_frame"])
        except (KeyError, TypeError, ValueError):
            continue

        if end_frame < start_frame:
            start_frame, end_frame = end_frame, start_frame
        excluded.update(range(start_frame, end_frame + 1))

    return excluded


def looks_single_scene(
    scores: np.ndarray,
    ratio_p99: float = 3.0,
    ratio_max: float = 6.0,
) -> bool:
    """True when the video is one continuous shot with no scene cuts.

    The threshold search always returns a split, even for a video with no
    dead-time to
    remove (an already-cut rally clip, or an inherently clean recording). On
    such input it invents a threshold inside a single population and shreds the
    clip. This guard catches that case first.

    A broadcast always contains high-motion frames -- scene cuts, replays, crowd
    pans -- so the score distribution has a long tail far above the stable
    match-camera level (measured: p99 4-47x the median, peaks 9-80x). A single
    continuous shot has none: every frame differs only slightly from the last
    (measured: p99 ~2x the median, peak ~2-3x). Requiring BOTH the 99th
    percentile and the maximum to stay close to the median keeps this firmly on
    the clean-clip side of a very wide gap, so it never fires on real matches.
    """
    if scores.size == 0:
        return False
    low = max(float(np.median(scores)), 1.0)
    p99 = float(np.percentile(scores, 99))
    peak = float(np.max(scores))
    return p99 < ratio_p99 * low and peak < ratio_max * low


def find_threshold_otsu3(scores: np.ndarray, nbins: int = 256) -> float:
    """Lower cut of a 3-class Otsu split on log10(MAD)."""
    if scores.size == 0:
        raise ValueError("scores is empty")

    values = scores.astype(float)
    log_scores = np.log10(np.maximum(values, 1.0))
    hist, edges = np.histogram(log_scores, bins=nbins)

    # Fewer than three occupied bins cannot carry three classes; any cut here
    # would be invented rather than found.
    if np.count_nonzero(hist) < 3:
        return float(values.max()) + 1.0

    p = hist.astype(float) / float(hist.sum())
    centers = (edges[:-1] + edges[1:]) / 2.0

    weight = np.cumsum(p)            # cumulative class weight up to each bin
    moment = np.cumsum(p * centers)  # cumulative first moment
    mu_total = moment[-1]

    best_variance = -1.0
    best_low_cut = 0
    j = np.arange(nbins - 1)
    for i in range(nbins - 2):
        upper = j[i + 1:]
        w0 = weight[i]
        w1 = weight[upper] - weight[i]
        w2 = 1.0 - weight[upper]
        valid = (w0 > 0) & (w1 > 0) & (w2 > 0)
        if not np.any(valid):
            continue

        m0 = moment[i] / w0
        m1 = np.where(valid, (moment[upper] - moment[i]) / np.where(w1 > 0, w1, 1.0), 0.0)
        m2 = np.where(valid, (mu_total - moment[upper]) / np.where(w2 > 0, w2, 1.0), 0.0)

        variance = (w0 * (m0 - mu_total) ** 2
                    + w1 * (m1 - mu_total) ** 2
                    + w2 * (m2 - mu_total) ** 2)
        variance = np.where(valid, variance, -1.0)

        best_here = float(np.max(variance))
        if best_here > best_variance:
            best_variance = best_here
            best_low_cut = i

    return float(10.0 ** centers[best_low_cut])


def threshold_near_distribution_edge(
    scores: np.ndarray,
    threshold: float,
    low_pct: float = 5.0,
    high_pct: float = 95.0,
) -> bool:
    """True when the split sits in the tail rather than between two populations."""
    if scores.size == 0:
        return False
    low = float(np.percentile(scores, low_pct))
    high = float(np.percentile(scores, high_pct))
    return threshold < low or threshold > high


def build_segments_from_mask(is_low: np.ndarray) -> list[tuple[int, int]]:
    """Turn a boolean low-motion mask into a list of (start, end) frame runs."""
    segments: list[tuple[int, int]] = []
    start_frame: int | None = None

    for frame_idx, low in enumerate(is_low):
        if low:
            if start_frame is None:
                start_frame = frame_idx
        elif start_frame is not None:
            segments.append((start_frame, frame_idx - 1))
            start_frame = None

    if start_frame is not None:
        segments.append((start_frame, len(is_low) - 1))

    return segments


def merge_close_segments(
    segments: list[tuple[int, int]],
    is_low: np.ndarray,
    min_ratio: float,
) -> list[tuple[int, int]]:
    """Merge adjacent segments when the gap between them is mostly low-motion."""
    if not segments:
        return []

    min_ratio = clamp(min_ratio, 0.0, 1.0)
    merged = [segments[0]]

    for start_frame, end_frame in segments[1:]:
        prev_start, prev_end = merged[-1]
        gap = start_frame - prev_end - 1

        if gap <= 0:
            merged[-1] = (prev_start, max(prev_end, end_frame))
            continue

        gap_start = prev_end + 1
        gap_end_exclusive = start_frame
        gap_mask = is_low[gap_start:gap_end_exclusive]
        low_count = int(np.sum(gap_mask))
        low_ratio = low_count / max(gap, 1)

        if low_ratio >= min_ratio:
            merged[-1] = (prev_start, max(prev_end, end_frame))
        else:
            merged.append((start_frame, end_frame))

    return merged


def merge_segments_by_gap(
    segments: list[tuple[int, int]],
    max_gap: int,
) -> list[tuple[int, int]]:
    """Final merge that only looks at frame distance between segments.

    Unlike ``merge_close_segments`` this ignores the low-motion ratio inside
    the gap: two segments whose gap (next.start - prev.end - 1) is < max_gap
    are joined. ``max_gap <= 0`` effectively disables this step (only merges
    overlapping/adjacent segments). Input must be sorted by start frame.
    """
    if not segments:
        return []

    merged = [segments[0]]

    for start_frame, end_frame in segments[1:]:
        prev_start, prev_end = merged[-1]
        gap = start_frame - prev_end - 1

        if gap < max_gap:
            merged[-1] = (prev_start, max(prev_end, end_frame))
        else:
            merged.append((start_frame, end_frame))

    return merged


def filter_short_segments(
    segments: list[tuple[int, int]],
    fps: float,
    min_seconds: float,
) -> list[tuple[int, int]]:
    """Drop segments shorter than ``min_seconds``."""
    if not segments:
        return []

    min_frames = int(np.ceil(min_seconds * max(fps, 1e-9)))
    kept: list[tuple[int, int]] = []

    for start_frame, end_frame in segments:
        seg_frames = end_frame - start_frame + 1
        if seg_frames >= min_frames:
            kept.append((start_frame, end_frame))

    return kept


def pick_two_middle_frames(start_frame: int, end_frame: int) -> tuple[int, int]:
    """Pick two representative frame indices near the middle of a segment."""
    boundaries = np.linspace(start_frame, end_frame + 1, 3)
    mids: list[int] = []

    for i in range(2):
        left = int(np.floor(boundaries[i]))
        right_exclusive = int(np.floor(boundaries[i + 1]))

        left = max(left, start_frame)
        right_exclusive = min(max(right_exclusive, left + 1), end_frame + 1)

        part_mid = (left + (right_exclusive - 1)) // 2
        mids.append(int(part_mid))

    return mids[0], mids[1]


def collect_required_frames(
    segments: list[tuple[int, int]],
) -> tuple[list[tuple[int, int]], set[int]]:
    """Return the representative frame pairs and the set of frames to decode."""
    pairs: list[tuple[int, int]] = []
    required: set[int] = set()

    for start_frame, end_frame in segments:
        m1, m2 = pick_two_middle_frames(start_frame, end_frame)
        pairs.append((m1, m2))
        required.add(m1)
        required.add(m2)

    return pairs, required


def suggest_avg_threshold_pct(segment_count: int) -> float:
    """Suggest a Cross_Diff_Avg widening percentage based on segment count."""
    n = max(segment_count, 0)
    if n <= 50:
        return 0.01 + 0.04 * (n / 50.0)
    if n <= 100:
        return 0.05 + 0.03 * ((n - 50) / 50.0)
    return min(0.08 + 0.02 * ((n - 100) / 100.0), 0.10)


def compute_avg_threshold(
    cross_avgs: list[int],
    segment_count: int,
    scale: float,
    fixed_pct: float | None,
) -> tuple[int, int, float]:
    """Compute the Cross_Diff_Avg keep threshold from the minimum average."""
    if not cross_avgs:
        return 0, 0, 0.0

    min_avg = int(min(cross_avgs))
    if fixed_pct is not None:
        pct = clamp(fixed_pct, 0.0, 0.20)
    else:
        pct = suggest_avg_threshold_pct(segment_count) * clamp(scale, 0.0, 1.0)

    threshold = round_to_int(min_avg * (1.0 + pct))
    return min_avg, threshold, pct


def reject_cross_outliers(
    segments: list[tuple[int, int]],
    cross_avgs: list[int],
    k: float,
    min_segments: int = 8,
    max_drop_ratio: float = 0.30,
) -> tuple[list[tuple[int, int]], list[int]]:
    """Drop segments whose Cross_Diff_Avg is a large outlier above the median.

    Once the obvious non-match candidates are gone, the surviving segments are
    overwhelmingly the one repeated match camera, so their cross averages form a
    tight cluster: across six matches every genuine rally sits within ~1.9x the
    median, while synthetic replays (Hawkeye "official review" renders and the
    like) land at ~5x. Anything above ``k`` * median is therefore a scene that
    merely *resembles* the court but is not live play, and is removed.

    This is deliberately conservative:

    * it needs at least ``min_segments`` segments for the median to be stable;
    * if more than ``max_drop_ratio`` of segments would be dropped it does
      nothing, since that means the "median" is not a clean match cluster.

    Returns the kept segments and the indices that were dropped.
    """
    n = len(segments)
    if n < min_segments or not cross_avgs:
        return segments, []

    median_avg = float(np.median(cross_avgs))
    if median_avg <= 0:
        return segments, []

    threshold = median_avg * float(k)
    dropped = [i for i, avg in enumerate(cross_avgs) if avg > threshold]
    if len(dropped) > max_drop_ratio * n:
        return segments, []

    dropped_set = set(dropped)
    kept = [seg for i, seg in enumerate(segments) if i not in dropped_set]
    return kept, dropped


def filter_segments_by_cross_avg(
    segments: list[tuple[int, int]],
    pairs: list[tuple[int, int]],
    cross_sums: list[int],
    cross_avgs: list[int],
    threshold: int,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[int], list[int]]:
    """Keep only segments whose Cross_Diff_Avg is <= threshold."""
    keep_indices = [idx for idx, avg in enumerate(cross_avgs) if avg <= threshold]

    filtered_segments = [segments[idx] for idx in keep_indices]
    filtered_pairs = [pairs[idx] for idx in keep_indices]
    filtered_cross_sums = [cross_sums[idx] for idx in keep_indices]
    filtered_cross_avgs = [cross_avgs[idx] for idx in keep_indices]

    return filtered_segments, filtered_pairs, filtered_cross_sums, filtered_cross_avgs
