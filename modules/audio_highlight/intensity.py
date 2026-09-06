"""Frozen Cheer Intensity v1: gated, within-match average ranks of log RMS."""
from __future__ import annotations

import math
import numpy as np
from numpy.typing import NDArray

RMS_EPSILON = 1e-12

class IntensityFeatureError(ValueError):
    pass

class CheerIntensityError(ValueError):
    pass

def rms_and_log_db(
    samples: object,
    *,
    epsilon: float = RMS_EPSILON,
) -> tuple[float, float]:
    """Return exact RMS definition and a finite epsilon-stabilized dB value."""

    values = np.asarray(samples)
    if values.ndim != 1 or values.size == 0:
        raise IntensityFeatureError("waveform must be a non-empty vector")
    if not np.issubdtype(values.dtype, np.number) or not np.isfinite(values).all():
        raise IntensityFeatureError("waveform must contain finite numeric samples")
    if not math.isfinite(epsilon) or epsilon <= 0:
        raise IntensityFeatureError("RMS epsilon must be positive and finite")
    floating = np.asarray(values, dtype=np.float64)
    rms = float(np.sqrt(np.mean(np.square(floating), dtype=np.float64)))
    log_rms_db = float(20.0 * np.log10(rms + epsilon))
    if not math.isfinite(rms) or not math.isfinite(log_rms_db):
        raise IntensityFeatureError("RMS result must be finite")
    return rms, log_rms_db



def average_rank_percentiles(values: object) -> NDArray[np.float64]:
    """Map finite values to [0, 1] using average ranks for exact ties."""

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or not np.isfinite(array).all():
        raise CheerIntensityError("rank input must be a finite one-dimensional vector")
    if array.size == 0:
        result = np.asarray([], dtype=np.float64)
        result.setflags(write=False)
        return result
    if array.size == 1:
        result = np.asarray([0.5], dtype=np.float64)
        result.setflags(write=False)
        return result
    order = np.argsort(array, kind="stable")
    sorted_values = array[order]
    ranks = np.empty(array.size, dtype=np.float64)
    start = 0
    while start < array.size:
        end = start + 1
        while end < array.size and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0
        start = end
    ranks /= array.size - 1
    ranks.setflags(write=False)
    return ranks
