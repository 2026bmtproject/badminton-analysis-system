"""Frozen Segment Aggregation v1; uses the upstream measurement contract."""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
import numpy as np

from modules.contracts import AudioSegmentSignals

P95_Q = 0.95
QUANTILE_METHOD = "linear"

class SegmentSignalsError(ValueError):
    """Raised when production segment-signal inputs violate their contract."""



@dataclass(frozen=True, slots=True)
class SegmentWindowSignal:
    """Canonical window measurement consumed by production aggregation."""

    match_id: str
    segment_index: int
    window_index_in_segment: int
    candidate_count_in_segment: int
    start_sec: float
    end_sec: float
    cheer_probability: float
    predicted_cheer: bool
    cheer_intensity: float | None
    intensity_method_id: str

    def __post_init__(self) -> None:
        if not self.match_id:
            raise SegmentSignalsError("match_id must not be empty")
        for field, value in (
            ("segment_index", self.segment_index),
            ("window_index_in_segment", self.window_index_in_segment),
            ("candidate_count_in_segment", self.candidate_count_in_segment),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise SegmentSignalsError(f"{field} must be an integer")
        if self.segment_index < 0 or self.window_index_in_segment < 0:
            raise SegmentSignalsError("window indices must be non-negative")
        if self.candidate_count_in_segment <= 0:
            raise SegmentSignalsError("candidate_count_in_segment must be positive")
        if (
            not math.isfinite(self.start_sec)
            or not math.isfinite(self.end_sec)
            or self.start_sec < 0
            or self.end_sec <= self.start_sec
        ):
            raise SegmentSignalsError("window timestamps must be finite and increasing")
        if not math.isfinite(self.cheer_probability) or not (
            0 <= self.cheer_probability <= 1
        ):
            raise SegmentSignalsError("cheer_probability must be in [0, 1]")
        if not isinstance(self.predicted_cheer, bool):
            raise SegmentSignalsError("predicted_cheer must be boolean")
        if self.cheer_intensity is not None and (
            not math.isfinite(self.cheer_intensity)
            or not 0 <= self.cheer_intensity <= 1
        ):
            raise SegmentSignalsError("cheer_intensity must be null or in [0, 1]")
        if self.predicted_cheer != (self.cheer_intensity is not None):
            raise SegmentSignalsError("detector gate and intensity null state disagree")
        if not self.intensity_method_id:
            raise SegmentSignalsError("intensity_method_id must not be empty")



def p95_linear(values: Sequence[float]) -> float:
    """Return the frozen q=.95 NumPy linear quantile for finite values."""

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0 or not np.isfinite(array).all():
        raise SegmentSignalsError("p95_linear requires finite non-empty values")
    return float(np.quantile(array, P95_Q, method=QUANTILE_METHOD))



def aggregate_segment_signals(
    windows: Sequence[SegmentWindowSignal],
    segment_indices: Sequence[int],
) -> tuple[AudioSegmentSignals, ...]:
    """Aggregate every known segment with the frozen asymmetric p95 supports."""

    expected = tuple(segment_indices)
    if not expected:
        raise SegmentSignalsError("segment inventory must not be empty")
    if any(
        isinstance(index, bool) or not isinstance(index, int) or index < 0
        for index in expected
    ):
        raise SegmentSignalsError("segment indices must be non-negative integers")
    if len(set(expected)) != len(expected):
        raise SegmentSignalsError("segment inventory contains duplicates")
    if not windows:
        raise SegmentSignalsError("window population must not be empty")

    expected_set = set(expected)
    grouped: dict[int, list[SegmentWindowSignal]] = defaultdict(list)
    identities: set[tuple[int, int]] = set()
    match_ids = set()
    method_ids = set()
    for window in windows:
        if window.segment_index not in expected_set:
            raise SegmentSignalsError(
                f"unknown segment_index in window population: {window.segment_index}"
            )
        identity = (window.segment_index, window.window_index_in_segment)
        if identity in identities:
            raise SegmentSignalsError(f"duplicate window identity: {identity}")
        identities.add(identity)
        grouped[window.segment_index].append(window)
        match_ids.add(window.match_id)
        method_ids.add(window.intensity_method_id)
    if len(match_ids) != 1:
        raise SegmentSignalsError("window population mixes match IDs")
    if len(method_ids) != 1:
        raise SegmentSignalsError("window population mixes intensity method IDs")

    missing = expected_set - set(grouped)
    if missing:
        raise SegmentSignalsError(
            f"segments without window records: {sorted(missing)}"
        )

    result: list[AudioSegmentSignals] = []
    for segment_index in sorted(expected):
        group = sorted(
            grouped[segment_index], key=lambda item: item.window_index_in_segment
        )
        if [window.window_index_in_segment for window in group] != list(
            range(len(group))
        ):
            raise SegmentSignalsError(
                f"segment {segment_index}: window indices must be contiguous from zero"
            )
        if any(window.candidate_count_in_segment != len(group) for window in group):
            raise SegmentSignalsError(
                f"segment {segment_index}: candidate count mismatch"
            )
        probabilities = [window.cheer_probability for window in group]
        intensities = [
            window.cheer_intensity
            for window in group
            if window.cheer_intensity is not None
        ]
        result.append(
            AudioSegmentSignals(
                segment_index=segment_index,
                cheer_confidence=p95_linear(probabilities),
                cheer_intensity=(
                    None if not intensities else p95_linear(intensities)
                ),
                n_cheer_windows=len(intensities),
            )
        )
    return tuple(result)
