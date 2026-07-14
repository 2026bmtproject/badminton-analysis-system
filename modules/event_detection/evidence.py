"""What BST's frame-by-frame scan says about a rally.

The dense scan asks BST "if there were a hit at frame f, what would it be?" for *every* f
of a rally, and gets back a 25-way probability vector each time (see
``modules.common.bst``). Around a real hit that vector locks onto one class for a run of
frames; over dead time it drifts and the model falls back on 未知球種. This module is the
only place in the stage that turns those probabilities into claims.

It exposes exactly two primitives, because the reference had four overlapping ones with
thresholds that had drifted apart:

* :meth:`Dense.lock_regions` — runs of the *same class*, gated on peak confidence. This is
  BST locking onto one stroke: the interval a single hit lives in.
* :meth:`Dense.runs` — runs of *any known class*, gated frame by frame, optionally
  filtered to one side or to serves. This is the interval a stroke signal *exists* over,
  and its **onset** is the anchor: a real hit sits right at the start of one, while a
  shuttle bouncing on the floor sits deep inside.

Everything else (:meth:`conf_near`, :meth:`side_at`, :meth:`onsets`, :meth:`has_serve`)
is derived from those two.

Also here: :meth:`side_map`, the per-frame top/bottom call. It is a rolling sum of the
p_top / p_bottom mass over the very same probabilities, so it lives with them. The
reference gave it a CSV artifact of its own, which bought nothing and created a file that
could go stale against the scan it was derived from.
"""

from __future__ import annotations

import collections

import numpy as np

from modules.common.bst.classes import (
    BOTTOM_INDICES,
    STROKE_CLASSES,
    TOP_INDICES,
    UNKNOWN_CLASS,
    to_side,
)

#: Every serve class is named 發短球 / 發長球, on either side — so the character is the test.
SERVE_MARK = "發"


class Run:
    """A consecutive stretch of frames the scan agrees about."""

    __slots__ = ("f0", "f1", "length", "mean_conf", "peak_conf", "wcentre", "labels")

    def __init__(self, items: list[tuple[int, str, float]]) -> None:
        confidences = [c for _, _, c in items]
        total = sum(confidences) or 1.0
        self.f0, self.f1 = items[0][0], items[-1][0]
        self.length = len(items)
        self.mean_conf = sum(confidences) / len(confidences)
        self.peak_conf = max(confidences)
        self.wcentre = sum(f * c for f, _, c in items) / total
        self.labels = [t for _, t, _ in items]

    @property
    def has_serve(self) -> bool:
        return any(SERVE_MARK in label for label in self.labels)


class Dense:
    """One segment's dense scan.

    Built straight from the ``(n_frames, 25)`` probability block the scan cached, plus the
    absolute frame that row 0 is. ``rows`` is the ``(frame, top1, conf)`` view the run
    finders walk; the raw probabilities stay around because the side derivation needs the
    full ``p_top`` / ``p_bottom`` mass, not just the winning class.
    """

    def __init__(self, probabilities: np.ndarray | None, start_frame: int = 0) -> None:
        if probabilities is None or len(probabilities) == 0:
            self.probabilities = np.zeros((0, len(STROKE_CLASSES)), dtype=np.float32)
            self.start_frame = start_frame
            self.rows: list[tuple[int, str, float]] = []
            return

        self.probabilities = np.asarray(probabilities, dtype=np.float32)
        self.start_frame = start_frame
        top1 = self.probabilities.argmax(axis=1)
        conf = self.probabilities.max(axis=1)
        self.rows = [
            (start_frame + i, STROKE_CLASSES[int(top1[i])], float(conf[i]))
            for i in range(len(self.probabilities))
        ]

    def __bool__(self) -> bool:
        return bool(self.rows)

    # ---- runs ---------------------------------------------------------------- #
    def lock_regions(self, min_run: int, conf_min: float) -> list[Run]:
        """Runs of one identical class, at least ``min_run`` long, peaking at ``conf_min``."""
        out: list[Run] = []
        i, n = 0, len(self.rows)
        while i < n:
            _, label, _ = self.rows[i]
            if label == UNKNOWN_CLASS:
                i += 1
                continue
            j = i
            while (
                j + 1 < n
                and self.rows[j + 1][1] == label
                and self.rows[j + 1][0] - self.rows[j][0] <= 1
            ):
                j += 1
            items = self.rows[i:j + 1]
            if len(items) >= min_run and max(c for _, _, c in items) >= conf_min:
                out.append(Run(items))
            i = j + 1
        return out

    def runs(self, conf_min: float, min_len: int, side: str | None = None) -> list[Run]:
        """Runs of any known class held above ``conf_min`` every frame.

        ``side`` restricts to one player's classes — which is how phase 3 asks "was the
        *other* player hitting anywhere in this gap?"
        """
        out: list[Run] = []
        current: list[tuple[int, str, float]] = []
        for frame, label, conf in self.rows:
            ok = (
                label != UNKNOWN_CLASS
                and conf >= conf_min
                and (side is None or to_side(label) == side)
            )
            if ok:
                current.append((frame, label, conf))
                continue
            if len(current) >= min_len:
                out.append(Run(current))
            current = []
        if len(current) >= min_len:
            out.append(Run(current))
        return out

    def onsets(self, conf_min: float = 0.5, min_len: int = 3) -> list[int]:
        """Where stroke signals *start*. See the module docstring: this is the anchor."""
        return [r.f0 for r in self.runs(conf_min, min_len)]

    # ---- point queries -------------------------------------------------------- #
    def conf_near(self, f: int, w: int = 3) -> float:
        """Highest confidence in any known class within ±``w`` frames."""
        return max(
            (c for frame, label, c in self.rows
             if abs(frame - f) <= w and label != UNKNOWN_CLASS),
            default=0.0,
        )

    def side_at(self, f: int, w: int = 4) -> str | None:
        """Confidence-weighted majority side of the top-1 labels within ±``w`` frames."""
        votes: collections.Counter = collections.Counter()
        for frame, label, conf in self.rows:
            side = to_side(label)
            if side and abs(frame - f) <= w:
                votes[side] += conf
        return max(votes, key=votes.get) if votes else None

    def has_serve(self, min_len: int = 3, conf_min: float = 0.4) -> bool:
        """Does any run in this segment contain a serve class?"""
        return any(r.has_serve for r in self.runs(conf_min, min_len))

    # ---- side derivation -------------------------------------------------------- #
    def side_map(self, win: int = 3, margin: float = 1.2) -> dict[int, str]:
        """``{frame: "top" | "bottom"}`` for the frames the scan is willing to call.

        Sums the *whole* Top mass and the whole Bottom mass over ±``win`` frames, rather
        than counting top-1 labels: a frame where BST is split three ways between Top
        strokes still says clearly that Top is hitting, and the argmax throws that away.
        A frame where neither side leads by ``margin`` x is left out of the map entirely —
        undecided, not guessed — and ``sides.SideOf`` falls back to the skeletons there.
        """
        if not len(self.probabilities):
            return {}
        p_top = self.probabilities[:, list(TOP_INDICES)].sum(axis=1)
        p_bottom = self.probabilities[:, list(BOTTOM_INDICES)].sum(axis=1)
        kernel = np.ones(2 * win + 1)
        top = np.convolve(p_top, kernel, mode="same")
        bottom = np.convolve(p_bottom, kernel, mode="same")

        out: dict[int, str] = {}
        for i in range(len(top)):
            frame = self.start_frame + i
            # Both tests can pass when the two sums are equal (margin >= 1 makes that a
            # tie neither wins outright); bottom is checked second and wins, exactly as
            # the reference's two successive assignments did.
            if top[i] >= margin * bottom[i]:
                out[frame] = "top"
            if bottom[i] >= margin * top[i]:
                out[frame] = "bottom"
        return out


def d_onset(f: int, onsets: list[int]) -> int:
    """Distance from ``f`` to the nearest run onset; 999 when there are none."""
    return min((abs(f - o) for o in onsets), default=999)
