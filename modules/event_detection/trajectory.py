"""One rally's shuttle trajectory, and the geometry the detector reads off it.

The single implementation of peaks, turns and amplitudes in this stage — every phase that
wants to know "how far did the shuttle swing around frame f" or "where are the arc tops"
comes through here, so those questions cannot be answered two subtly different ways.

The one substantive change from the reference implementation is what counts as a missing
point: it read CSVs where an untracked frame was written as ``x == 0`` and keyed off that
sentinel, while ``shuttle.json`` says so outright with
``visible: false`` / ``x: null``. Frames stay dense (there is one record per frame of the
segment, tracked or not); it is the *valid* points that are sparse, and the ``v*`` arrays
below are the compacted view of them that every signal is computed on.
"""

from __future__ import annotations

import bisect
import math

import numpy as np
from scipy.signal import find_peaks


def angle_between(v1, v2) -> float:
    """Angle (degrees, 0..180) between two directed segments ``(x0, y0, x1, y1)``."""
    a1 = int(math.atan2(v1[3] - v1[1], v1[2] - v1[0]) * 180 / math.pi)
    a2 = int(math.atan2(v2[3] - v2[1], v2[2] - v2[0]) * 180 / math.pi)
    if a1 * a2 >= 0:
        return abs(a1 - a2)
    inc = abs(a1) + abs(a2)
    return 360 - inc if inc > 180 else inc


class Traj:
    """One segment's trajectory. ``frames`` must be consecutive integers.

    ``visible`` marks which frames the tracker actually saw the shuttle in; the others
    have no coordinates and are simply absent from ``at`` and from the compacted arrays.
    """

    def __init__(
        self,
        frames: list[int],
        visible: list[bool],
        xs: list[float],
        ys: list[float],
    ) -> None:
        self.frames = frames
        self.visible = visible
        self.xs = xs
        self.ys = ys
        self.f0 = frames[0] if frames else 0

        keep = [i for i in range(len(frames)) if visible[i]]
        self.at = {frames[i]: (xs[i], ys[i]) for i in keep}
        self.vf = [frames[i] for i in keep]
        self.vx = np.array([xs[i] for i in keep], dtype=float)
        self.vy = np.array([ys[i] for i in keep], dtype=float)

    def __len__(self) -> int:
        return len(self.frames)

    # ---- amplitude ---------------------------------------------------------- #
    def amp(self, f: int, win: int = 18) -> tuple[float, float] | None:
        """``(yamp, xamp)`` over ±``win`` frames. Fewer than 5 valid points -> None.

        None is not zero: it means "cannot say", and every gate treats it as a pass. A
        stretch of rally the tracker lost is not evidence that nothing happened there.
        """
        ys = [self.at[k][1] for k in range(f - win, f + win + 1) if k in self.at]
        xs = [self.at[k][0] for k in range(f - win, f + win + 1) if k in self.at]
        if len(ys) < 5:
            return None
        return max(ys) - min(ys), max(xs) - min(xs)

    def amp_pass(self, f: int, yamp_min: float, xamp_min: float, win: int = 18) -> bool:
        a = self.amp(f, win)
        return a is None or a[0] >= yamp_min or a[1] >= xamp_min

    # ---- peaks / turning points --------------------------------------------- #
    def y_peaks(self, prom: float) -> dict[int, float]:
        """Tops of the arcs the shuttle was hit into -> ``{frame: prominence}``.

        Image y grows downward, so a *peak* in y is the shuttle at its lowest — which is
        where a player met it. That inversion is the whole reason this reads as a peak
        rather than a valley.
        """
        if len(self.vy) < 3:
            return {}
        peaks, props = find_peaks(self.vy, prominence=prom)
        return {
            self.vf[p]: float(props["prominences"][i]) for i, p in enumerate(peaks)
        }

    def x_turns(self, prom: float) -> dict[int, float]:
        """Horizontal reversals -> ``{frame: prominence}``. A hit must change ``vx``."""
        if len(self.vx) < 5:
            return {}
        out: dict[int, float] = {}
        for signal in (self.vx, -self.vx):
            peaks, props = find_peaks(signal, prominence=prom)
            for i, p in enumerate(peaks):
                out[self.vf[p]] = max(
                    out.get(self.vf[p], 0.0), float(props["prominences"][i])
                )
        return out

    def first_rise(self, min_len: int = 6, min_rise: float = 60) -> int | None:
        """Start of the first sustained rise = the serve leaving the racket.

        "Sustained" is: image y falls for at least ``min_len`` steps (gaps of up to 3
        frames tolerated, and a 2 px wobble does not break the run) and climbs at least
        ``min_rise`` px in total. None if the rally has no such stretch.
        """
        points = list(zip(self.vf, self.vy))
        for i in range(len(points)):
            j, rise = i, 0.0
            while (
                j + 1 < len(points)
                and points[j + 1][0] - points[j][0] <= 3
                and points[j + 1][1] <= points[j][1] + 2
            ):
                rise += max(0.0, points[j][1] - points[j + 1][1])
                j += 1
                if j - i >= min_len and rise >= min_rise:
                    return points[i][0]
        return None

    # ---- per-point measurements, for the debug CSV --------------------------- #
    def signal_values(self, f: int, accel_w: int = 3, ramp_w: int = 3, amp_win: int = 18) -> dict:
        """The accel/ramp/amplitude numbers behind one frame's decision."""
        out: dict = {}
        k = bisect.bisect_left(self.vf, f)
        if k >= len(self.vf):
            k = len(self.vf) - 1
        if k > 0 and abs(self.vf[k - 1] - f) <= abs(self.vf[k] - f):
            k -= 1
        if k < 0:
            return {"yamp": None, "xamp": None}

        X, Y = self.vx, self.vy
        if 0 <= k - accel_w and k + accel_w < len(self.vf):
            w = accel_w
            sp_in = math.hypot(X[k] - X[k - w], Y[k] - Y[k - w])
            sp_out = math.hypot(X[k + w] - X[k], Y[k + w] - Y[k])
            lo = min(sp_in, sp_out)
            out["accel_ratio"] = round(max(sp_in, sp_out) / lo, 2) if lo else None
            out["accel_angle"] = angle_between(
                [X[k - w], Y[k - w], X[k], Y[k]], [X[k], Y[k], X[k + w], Y[k + w]]
            )
        if 0 <= k - ramp_w and k + ramp_w < len(self.vf):
            w = ramp_w
            before = [
                math.hypot(X[j] - X[j - 1], Y[j] - Y[j - 1]) for j in range(k - w + 1, k + 1)
            ]
            after = [
                math.hypot(X[j] - X[j - 1], Y[j] - Y[j - 1]) for j in range(k + 1, k + w + 1)
            ]
            avg_b, avg_a = sum(before) / w, sum(after) / w
            out["ramp_ratio"] = round(avg_a / avg_b, 2) if avg_b > 0 else None
            out["ramp_dvx"] = round(
                abs((X[k + w] - X[k]) / w - (X[k] - X[k - w]) / w), 1
            )
        a = self.amp(f, amp_win)
        out["yamp"], out["xamp"] = a if a else (None, None)
        return out


def build_trajectories(
    records: list[dict], method: str
) -> dict[int, Traj]:
    """``shuttle.json`` records -> one :class:`Traj` per segment, for one ``method``.

    Returns only segments that have at least one record. A segment whose trajectory is
    entirely invisible still yields a ``Traj`` (an empty one); the caller decides what
    that means, which is not the same as the segment being absent.
    """
    by_segment: dict[int, list[dict]] = {}
    for record in records:
        if record.get("method") != method:
            continue
        by_segment.setdefault(int(record["segment_index"]), []).append(record)

    trajectories: dict[int, Traj] = {}
    for index, rows in by_segment.items():
        rows.sort(key=lambda r: int(r["frame"]))
        frames = [int(r["frame"]) for r in rows]
        visible = [bool(r.get("visible")) and r.get("x") is not None for r in rows]
        xs = [float(r["x"]) if v else 0.0 for r, v in zip(rows, visible)]
        ys = [float(r["y"]) if v else 0.0 for r, v in zip(rows, visible)]
        trajectories[index] = Traj(frames, visible, xs, ys)
    return trajectories
