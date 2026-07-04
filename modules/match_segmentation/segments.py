"""Segment math plus the segments.json contract this stage produces.

The math functions (GMM thresholding, building, merging, filtering) touch
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


def find_threshold_gmm(scores: np.ndarray) -> float:
    """Fit a 2-component GMM in log space and return the split threshold."""
    if scores.size == 0:
        raise ValueError("scores is empty")

    log_scores = np.log10(np.maximum(scores.astype(float), 1.0))

    m1 = float(np.percentile(log_scores, 25))
    m2 = float(np.percentile(log_scores, 75))
    shared_var = float(np.var(log_scores))
    v1 = max(shared_var, 1e-6)
    v2 = max(shared_var, 1e-6)
    w1, w2 = 0.5, 0.5

    for _ in range(100):
        p1 = w1 * np.exp(-0.5 * ((log_scores - m1) ** 2) / v1) / np.sqrt(2.0 * np.pi * v1)
        p2 = w2 * np.exp(-0.5 * ((log_scores - m2) ** 2) / v2) / np.sqrt(2.0 * np.pi * v2)
        denom = np.maximum(p1 + p2, 1e-12)

        r1 = p1 / denom
        r2 = p2 / denom

        n1 = max(float(np.sum(r1)), 1e-9)
        n2 = max(float(np.sum(r2)), 1e-9)

        m1 = float(np.sum(r1 * log_scores) / n1)
        m2 = float(np.sum(r2 * log_scores) / n2)

        v1 = max(float(np.sum(r1 * (log_scores - m1) ** 2) / n1), 1e-9)
        v2 = max(float(np.sum(r2 * (log_scores - m2) ** 2) / n2), 1e-9)

        total = max(float(log_scores.size), 1e-9)
        w1 = n1 / total
        w2 = n2 / total

    means = np.array([m1, m2], dtype=float)
    variances = np.array([v1, v2], dtype=float)
    weights = np.array([w1, w2], dtype=float)

    order = np.argsort(means)
    m1, m2 = means[order]
    v1, v2 = variances[order]
    w1, w2 = weights[order]

    v1 = max(float(v1), 1e-12)
    v2 = max(float(v2), 1e-12)

    s1 = np.sqrt(v1)
    s2 = np.sqrt(v2)

    a = 1.0 / (2.0 * v1) - 1.0 / (2.0 * v2)
    b = m2 / v2 - m1 / v1
    c = (m1**2) / (2.0 * v1) - (m2**2) / (2.0 * v2) + np.log((s2 * w1) / (s1 * w2))

    roots = np.roots([a, b, c])
    real_roots = np.real(roots[np.isreal(roots)])
    between = real_roots[(real_roots > m1) & (real_roots < m2)]

    if between.size > 0:
        thresh_log = float(between[0])
    else:
        thresh_log = float((m1 + m2) / 2.0)

    return float(10.0**thresh_log)


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
