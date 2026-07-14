"""Phase 1 — pulling hit candidates out of one trajectory.

Five signals, each looking for a different fingerprint a racket leaves on the shuttle's
path. Deliberately over-eager: phase 2 throws candidates away, phase 3 fills in what all
five missed, and a hit that never becomes a candidate here can still be recovered there —
but only if *some* stream saw it.

  serve-drop  the first steep fall — the shuttle being dropped/tossed for the serve
  serve-rise  the first sustained rise — the serve leaving the racket. A second serve
              anchor exists because a bottom-court player's toss is routinely hidden
              behind their own body, and serve-drop simply never fires on it.
  ypeak       the top of an arc the shuttle was hit into (image y is inverted, so a
              trough in the picture is a peak in the array)
  accel       near the valley between two arcs: did the velocity vector turn hard *and*
              change magnitude? That is a racket, not gravity.
  ramp        a sliding scan for slow-then-fast with a change in horizontal speed —
              catches flat drives, which have no arc for ypeak to find.

"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import find_peaks

from modules.event_detection.config import SignalConfig
from modules.event_detection.trajectory import Traj, angle_between


def _trim_edge_peaks(peaks: np.ndarray, y: np.ndarray, edge_prom: float) -> np.ndarray:
    """Drop the first/last Y peak when the valley beside it is too shallow to believe.

    The peaks at the two ends of a rally are the least trustworthy: the shuttle is entering
    or leaving the tracker's view, and a shallow bump there is usually the trajectory
    settling rather than a racket. So each is measured against the valley between it and
    its neighbour, and dropped if it barely rises out of it.

    Every version of this before v632 compared the peak's height against
    ``np.argmin(...)`` — the valley's *index* — where it meant the value at that index. On
    ASG_vs_AA_2020 that made the test ``y[peak] (~540 px) - a frame offset (~12) < 5``,
    which is false for any real arc bottom: the trim **never fired once in 112 rallies**,
    and the function was dead code. The reading below is the one the comparison was always
    supposed to make; it fires 3 times in that match, and moves exactly one hit of 947 (by
    3 frames). It was measured before it was changed.
    """
    if len(peaks) < 5:
        return peaks
    valley = y[peaks[0] + np.argmin(y[peaks[0]:peaks[1]])]
    if (y[peaks[0]] - valley) < edge_prom:
        peaks = np.delete(peaks, 0)
    valley = y[peaks[-2] + np.argmin(y[peaks[-2]:peaks[-1]])]
    if (y[peaks[-1]] - valley) < edge_prom:
        peaks = np.delete(peaks, -1)
    return peaks


def detect_candidates(traj: Traj, cfg: SignalConfig) -> tuple[list[int], dict[int, set[str]]]:
    """-> (candidate frames, ascending; ``{frame: {signal names that fired}}``)."""
    x, y = traj.vx, traj.vy
    if len(x) == 0:
        return [], {}

    z = np.array(traj.vf)          # compacted index -> frame number
    pos = z - traj.f0              # compacted index -> row in the dense frame range
    n = len(traj.frames)
    predict = np.zeros(n)
    turning = np.zeros(n)
    contrib: dict[int, set[str]] = {}

    def add(row, reason: str) -> None:
        contrib.setdefault(int(row), set()).add(reason)

    # ---- serve-drop: the first steep fall ------------------------------------- #
    start_row = 0
    for i in range(len(y) - 1):
        if z[i + 1] == z[i]:
            continue
        if (y[i] - y[i + 1]) / (z[i + 1] - z[i]) >= cfg.serve_drop_speed:
            start_row = int(pos[i])
            predict[start_row] = 1
            add(start_row, "serve")
            break

    # ---- ypeak (only after the serve anchor) ----------------------------------- #
    peaks = _trim_edge_peaks(
        find_peaks(y, prominence=cfg.ypeak_prom)[0], y, cfg.ypeak_edge_prom
    )
    for p in peaks:
        if pos[p] >= start_row:
            predict[pos[p]] = 1
            add(pos[p], "ypeak")

    # ---- accel: turn + speed change near the valley between two arcs ----------- #
    w = cfg.accel_win
    for i in range(len(peaks) - 1):
        start, end = peaks[i], peaks[i + 1] + 1
        valley = start + int(np.argmin(y[start:end]))
        for j in range(valley, end + 1):
            if not (j - valley > 5 and end - j > 5):
                continue
            if not (j - w >= 0 and j + w < len(x)):
                continue
            speed_in = math.hypot(x[j] - x[j - w], y[j] - y[j - w])
            speed_out = math.hypot(x[j + w] - x[j], y[j + w] - y[j])
            lo = min(speed_in, speed_out)
            ratio = max(speed_in, speed_out) / lo if lo > 0 else float("inf")
            turn = angle_between(
                [x[j - w], y[j - w], x[j], y[j]], [x[j], y[j], x[j + w], y[j + w]]
            )
            if turn > cfg.accel_angle and ratio >= cfg.accel_ratio:
                turning[pos[j]] = 1
    for row in find_peaks(turning, distance=cfg.accel_dist)[0]:
        predict[row] = 1
        add(row, "accel")

    # ---- ramp: slow then fast, with the horizontal speed changing -------------- #
    rw = cfg.ramp_win
    for j in range(rw, len(x) - rw):
        before = [
            math.hypot(x[k] - x[k - 1], y[k] - y[k - 1]) for k in range(j - rw + 1, j + 1)
        ]
        after = [
            math.hypot(x[k] - x[k - 1], y[k] - y[k - 1]) for k in range(j + 1, j + rw + 1)
        ]
        avg_before, avg_after = sum(before) / rw, sum(after) / rw
        if avg_before <= 0 or avg_before >= cfg.ramp_speed_max:
            continue
        if avg_after / avg_before < cfg.ramp_ratio:
            continue
        dvx = abs((x[j + rw] - x[j]) / rw - (x[j] - x[j - rw]) / rw)
        if dvx < cfg.ramp_dvx:
            continue
        predict[pos[j]] = 1
        add(pos[j], "ramp")

    # ---- deduplicate the stacked signals --------------------------------------- #
    rows = list(find_peaks(predict, distance=max(1, cfg.min_gap))[0])

    # serve-rise is added *after* the dedup so its min_gap suppression cannot swallow a
    # neighbouring real signal — it is an anchor, not a competitor.
    rise = traj.first_rise(cfg.serve_rise_len, cfg.serve_rise_px)
    if rise is not None:
        row = rise - traj.f0
        if all(abs(row - other) >= cfg.min_gap for other in rows):
            rows.append(row)
            add(row, "serve_rise")
    rows.sort()

    reasons: dict[int, set[str]] = {}
    for row in rows:
        fired: set[str] = set()
        for k, names in contrib.items():
            if abs(k - int(row)) <= 5:
                fired |= names
        reasons[traj.f0 + int(row)] = fired or {"ypeak"}
    return [traj.f0 + int(row) for row in rows], reasons
