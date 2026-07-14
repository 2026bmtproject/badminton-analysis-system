"""Which player hit it: BST first, the skeletons as backup.

The **only** side lookup in the stage. Candidate selection, gap filling and pruning all
alternate-hit reasoning depends on this answering consistently — v632's predecessor had
two implementations (one with the skeleton fallback, one without) used by different rules,
and hits appeared and disappeared depending on which one a rule happened to call.

Two sources, in order:

1. **BST.** ``evidence.Dense.side_map`` decides most frames outright. A frame it left
   undecided is looked up ±``snap`` frames away first — the scan is a sliding window, so a
   neighbour's answer is very nearly this frame's answer.
2. **The skeletons.** Distance from the shuttle to the nearest wrist, *divided by that
   player's arm length*. Perspective compresses the far half of the court, so the far
   player is small in frame and everything looks close to them in pixels — raw distances
   systematically flatter them. Measured in the only unit that means anything, the
   player's own scale, 90 px from a far player whose whole arm is 100 px is a miss, while
   120 px from a near player whose arm is 300 px is contact. Whoever is closer in
   arm-lengths hit it; if the two are within ``margin`` x of each other it is too close to
   call, and ±``win`` frames are searched for a moment where it is not.

Both can decline. None means nobody knows, and every caller treats that as "do not use
the alternation argument here" rather than guessing.
"""

from __future__ import annotations

import bisect
import math

#: A keypoint scored below this is not worth measuring against.
MIN_SCORE = 0.2

Joints = dict[str, tuple[float, float, float]]


# ---- skeleton geometry ------------------------------------------------------- #
def _seg_len(a, b) -> float | None:
    if a is None or b is None or a[2] <= MIN_SCORE or b[2] <= MIN_SCORE:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _arm_len(joints: Joints, lr: str) -> float | None:
    shoulder = joints.get(lr + "_shoulder")
    elbow = joints.get(lr + "_elbow")
    wrist = joints.get(lr + "_wrist")
    total = (_seg_len(shoulder, elbow) or 0.0) + (_seg_len(elbow, wrist) or 0.0)
    return total if total > 0 else _seg_len(shoulder, wrist)


def _player_arm_len(joints: Joints) -> float | None:
    """The player's scale. Falls back to 1.5x shoulder width when no arm is readable."""
    arms = [a for a in (_arm_len(joints, "L"), _arm_len(joints, "R")) if a]
    if arms:
        return max(arms)
    width = _seg_len(joints.get("L_shoulder"), joints.get("R_shoulder"))
    return width * 1.5 if width else None


def _nearest_wrist_dist(joints: Joints, ball: tuple[float, float]) -> float | None:
    best = None
    for name in ("L_wrist", "R_wrist"):
        wrist = joints.get(name)
        if wrist is None or wrist[2] <= MIN_SCORE:
            continue
        d = math.hypot(ball[0] - wrist[0], ball[1] - wrist[1])
        best = d if best is None or d < best else best
    return best


def _avg_joints(a: Joints | None, b: Joints | None) -> Joints | None:
    """Blend the nearest usable skeleton before and after a frame the player was lost in."""
    if a is None or b is None:
        return a or b
    out: Joints = {}
    for name in set(a) | set(b):
        ja, jb = a.get(name), b.get(name)
        out[name] = (
            ((ja[0] + jb[0]) / 2, (ja[1] + jb[1]) / 2, min(ja[2], jb[2]))
            if ja and jb
            else (ja or jb)
        )
    return out


def _pose_ok(joints: Joints | None) -> bool:
    if not joints or not _player_arm_len(joints):
        return False
    return any(
        joints.get(n) and joints[n][2] > MIN_SCORE for n in ("L_wrist", "R_wrist")
    )


def _decide(dist_top: float | None, dist_bottom: float | None, margin: float) -> str | None:
    """Normalized wrist distances -> a side, or None when they are too close to call."""
    if dist_top is None and dist_bottom is None:
        return None
    if dist_bottom is None:
        return "top"
    if dist_top is None:
        return "bottom"
    lo = min(dist_top, dist_bottom)
    if lo > 0 and max(dist_top, dist_bottom) / lo < margin:
        return None
    return "top" if dist_top < dist_bottom else "bottom"


class SideOf:
    """``side_of(frame) -> "top" | "bottom" | None``, memoized."""

    def __init__(
        self,
        bst_map: dict[int, str] | None,
        skeletons: dict[int, dict[str, Joints]] | None,
        ball_at: dict[int, tuple[float, float]],
        snap: int = 4,
        margin: float = 1.3,
        win: int = 2,
    ) -> None:
        self.bst = bst_map or {}
        self.snap = snap
        self.margin = margin
        self.win = win
        self.ball_at = ball_at
        self.skeletons = skeletons
        self._cache: dict[int, str | None] = {}

        available: dict[str, list[int]] = {}
        for frame, players in (skeletons or {}).items():
            for player, joints in players.items():
                if _pose_ok(joints):
                    available.setdefault(player, []).append(frame)
        self._available = {p: sorted(v) for p, v in available.items()}

    # ---- skeleton lookup ------------------------------------------------------ #
    def _joints(self, frame: int, player: str) -> Joints | None:
        joints = (self.skeletons or {}).get(frame, {}).get(player)
        if _pose_ok(joints):
            return joints
        frames = self._available.get(player)
        if not frames:
            return None
        i = bisect.bisect_left(frames, frame)
        before = self.skeletons[frames[i - 1]][player] if i > 0 else None
        after = self.skeletons[frames[i]][player] if i < len(frames) else None
        return _avg_joints(before, after)

    def _norm_dist(self, frame: int, player: str) -> float | None:
        ball = self.ball_at.get(frame)
        if ball is None:
            return None
        joints = self._joints(frame, player)
        if not joints:
            return None
        wrist = _nearest_wrist_dist(joints, ball)
        arm = _player_arm_len(joints)
        return wrist / arm if wrist is not None and arm else None

    def _skeleton_side(self, frame: int) -> str | None:
        top = self._norm_dist(frame, "top")
        bottom = self._norm_dist(frame, "bottom")
        side = _decide(top, bottom, self.margin)
        if side is not None:
            return side

        # Too close to call here: take the clearest nearby frame instead — the one where
        # some wrist is closest to the shuttle in its own arm-lengths.
        best: tuple[float, str] | None = None
        for w in range(frame - self.win, frame + self.win + 1):
            if w == frame:
                continue
            a, b = self._norm_dist(w, "top"), self._norm_dist(w, "bottom")
            side_w = _decide(a, b, self.margin)
            if side_w is None:
                continue
            d = a if side_w == "top" else b
            if d is not None and (best is None or d < best[0]):
                best = (d, side_w)
        return best[1] if best else None

    # ---- public ---------------------------------------------------------------- #
    def __call__(self, frame: int) -> str | None:
        if frame in self._cache:
            return self._cache[frame]
        side = None
        for w in range(self.snap + 1):
            side = self.bst.get(frame - w) or self.bst.get(frame + w)
            if side:
                break
        if not side and self.skeletons:
            side = self._skeleton_side(frame)
        self._cache[frame] = side
        return side

    @staticmethod
    def opposite(side: str | None) -> str | None:
        return {"top": "bottom", "bottom": "top"}.get(side)


def skeletons_by_segment(
    records: list[dict], keypoint_names: tuple[str, ...]
) -> dict[int, dict[int, dict[str, Joints]]]:
    """``pose.json`` records -> ``{segment: {frame: {player: {joint: (x, y, score)}}}}``.

    A frame where a player was not found carries ``keypoints: null``; that player is simply
    absent from the frame's dict, which is what :meth:`SideOf._joints` looks for before it
    reaches for a neighbouring frame.
    """
    out: dict[int, dict[int, dict[str, Joints]]] = {}
    for record in records:
        keypoints = record.get("keypoints")
        if keypoints is None:
            continue
        segment = int(record["segment_index"])
        frame = int(record["frame"])
        joints: Joints = {
            name: (float(kp[0]), float(kp[1]), float(kp[2]))
            for name, kp in zip(keypoint_names, keypoints)
        }
        out.setdefault(segment, {}).setdefault(frame, {})[record["player"]] = joints
    return out
