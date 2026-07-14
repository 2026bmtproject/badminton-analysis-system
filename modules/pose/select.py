"""Pick the two players out of everyone the detector found.

A broadcast frame contains line judges, the umpire, ball kids and the crowd. What
separates the players is *where they stand*: on the court. So each detected person is
reduced to a ground point (their feet), that point is mapped through the court
homography into court metres, and only people standing on the court survive. The
survivor in the far half is ``top``, the one in the near half is ``bottom``.

Why the court has to be enlarged
--------------------------------
The homography is a mapping of the **ground plane**. It is only correct for points
that are actually on the ground — and a player mid-smash is not. Their ankles are up
in the air, so back-projecting through the ground plane does not return where they
jumped from: the ray from the camera through raised ankles hits the ground plane
*beyond* the player, pushing them further up-court. For the far (``top``) player that
means their feet land past the far baseline — outside the court, ``y < 0`` — and a
strict in-court test would drop the player exactly on the frames a smash happens,
which are the frames that matter most.

The fix is to accept a band around the court rather than the court itself, and to make
it asymmetric: generous along the baselines (:attr:`SelectConfig.y_margin`), where the
jump artifact points, and tight across the sidelines
(:attr:`SelectConfig.x_margin`), where the line judges sit and where a jump barely
moves anything. Both are fractions of the court's own size, so they are
resolution- and camera-independent.

The margins are the *acceptance* test, not the ranking: with the court widened, more
non-players can slip in, so within each half the candidates are ranked and only the
best one is taken. See :func:`select_players`.

Why size has to be measured on the court, not in pixels
-------------------------------------------------------
The one non-player the widened court cannot keep out is the **umpire**, who sits on a
raised chair beside the net — inside the sideline margin by construction, and there in
every single frame. Ranking the half by pixel area hands them the far player's slot
almost every time: on the test match, area put the real player ahead of them in only
13.6% of the frames the two shared.

Pixel size says nothing about who is a player, because perspective makes whoever is
nearest the camera the biggest. The umpire is *closer* than the far player, so they are
*bigger* — 22.1k px² against 19.2k — while being neither a player nor even
player-shaped. Anything that ranks by apparent size in pixels is measuring distance from
the camera.

:func:`court_size` removes that by dividing each person's apparent height by the size of
one court metre *at the point they are standing on* (:func:`ground_scale`), which the
homography already knows. Two people of equal real size then score equally wherever they
stand, and the comparison becomes about them rather than about the lens.

It also turns the umpire's chair from a disguise into a tell. The homography is a
ground-plane map, so a person 1.5 m up in the air back-projects to a ground point that
is not theirs — for the umpire, to the net line, where the plane is steeply foreshortened
and one metre is worth 47 px rather than the 32 px of the far court. A body genuinely
standing there would tower; the umpire does not, and scores 3.9 against a real player's
5.6. The same arithmetic *rewards* an airborne player, whose feet project the other way,
past the baseline where a metre is worth less — so the ranking is at its most confident
on exactly the smash frames the margins were widened for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from modules.contracts import POSE_PLAYERS
from modules.court_detection.detector import H_LINES, V_LINES

#: The court's own dimensions, in metres, as the detector's homography defines them.
#: Sourced from the detector rather than re-typed so the two can never drift apart.
COURT_WIDTH_M = float(V_LINES[-1])    # 6.10 — sideline to sideline
COURT_LENGTH_M = float(H_LINES[-1])   # 13.41 — baseline to baseline

#: COCO-17 indices used to find the ground point of a person.
L_ANKLE, R_ANKLE = 15, 16

#: ("top", "bottom") — the order every function here returns the two players in.
PLAYERS = POSE_PLAYERS

#: How far outside the court someone can be and still be *worth posing*. See
#: :func:`candidate_mask`. These must stay strictly looser than the selection margins,
#: not merely equal to them: the pre-filter judges from the bounding box while the
#: selection judges from the ankles, so the two disagree by a few pixels — and a
#: candidate wrongly discarded here has no skeleton left to reconsider. The gap is the
#: room for that disagreement.
CANDIDATE_X_MARGIN = 0.35
CANDIDATE_Y_MARGIN = 0.45


@dataclass
class SelectConfig:
    """Knobs for turning detections into two players.

    Both margins are sized from measurements on real footage, not guessed:

    * ``x_margin`` 0.25 (~1.5 m outside a sideline). Players lunge well clear of the
      court chasing a wide shot — measured up to 1.74 m out, median 0.84 m — and a tight
      sideline is what loses them. This is the margin that matters, and it is *not*
      free: the line judges sit at roughly this distance, which is why the selection is
      ranked by :class:`PlayerTracker` rather than by proximity alone.
    * ``y_margin`` 0.25 (~3.4 m past a baseline). Sized for the jump artifact described
      above; measured airborne players reach -0.21. Widening it further buys nothing —
      on the test match, no player was ever lost to the baseline margin.

    ``min_ankle_score`` decides when the ankles are too unreliable to stand on and the
    bottom edge of the bounding box is used as the ground point instead.

    ``max_step_px`` and ``prior_max_age`` gate the tracker; see :class:`PlayerTracker`.
    """

    x_margin: float = 0.25           # fraction of court width, outside each sideline
    y_margin: float = 0.25           # fraction of court length, outside each baseline
    min_ankle_score: float = 0.3
    max_step_px: float = 120.0       # how far a player may move per frame of prior age
    prior_max_age: int = 5           # frames after which a prior is stale and dropped


def court_from_image(homography) -> np.ndarray:
    """Invert ``court.json``'s homography into the image -> court-metres direction.

    ``court_detection`` fits and stores court -> image (that is the direction it needs
    to draw key points); asking "which court position is this pixel?" is the other way
    round.
    """
    matrix = np.asarray(homography, dtype=np.float64)
    if matrix.shape != (3, 3):
        raise ValueError(f"homography must be 3x3, got {matrix.shape}")
    try:
        return np.linalg.inv(matrix)
    except np.linalg.LinAlgError as e:
        raise ValueError("court homography is singular and cannot be inverted") from e


def ground_points(det: dict, min_ankle_score: float) -> np.ndarray:
    """The ``(n, 2)`` image point each person is standing on.

    Normally the midpoint of the two ankles. When neither ankle is confidently placed —
    occluded by the net, cut off at the frame edge, lost in a lunge — the midpoint is
    garbage and would project somewhere arbitrary, so the bottom-centre of the bounding
    box is used instead: less precise, but it is at least on the person.
    """
    kps, scores, bboxes = det["kps"], det["scores"], det["bboxes"]
    if len(kps) == 0:
        return np.zeros((0, 2), np.float64)

    ankles = kps[:, [L_ANKLE, R_ANKLE], :].astype(np.float64)          # (n, 2, 2)
    ankle_scores = scores[:, [L_ANKLE, R_ANKLE]].astype(np.float64)    # (n, 2)

    usable = ankle_scores >= min_ankle_score                           # (n, 2)
    weights = usable.astype(np.float64)
    count = weights.sum(axis=1)                                        # (n,)

    # Mean of whichever ankles are trustworthy (one is enough).
    safe = np.maximum(count, 1)[:, None]
    points = (ankles * weights[:, :, None]).sum(axis=1) / safe

    fallback = np.stack(
        [(bboxes[:, 0] + bboxes[:, 2]) / 2.0, bboxes[:, 3]], axis=1
    ).astype(np.float64)
    return np.where((count == 0)[:, None], fallback, points)


def to_court(points: np.ndarray, image_to_court: np.ndarray) -> np.ndarray:
    """Project image points to normalized court coordinates.

    Returns ``(n, 2)`` where x runs 0..1 across the width (left to right sideline) and
    y runs 0..1 along the length (far baseline to near baseline). Outside the court the
    values simply run past 0 and 1, which is what the margins are measured in.
    """
    if len(points) == 0:
        return np.zeros((0, 2), np.float64)
    homogeneous = np.hstack([points, np.ones((len(points), 1))])       # (n, 3)
    projected = homogeneous @ image_to_court.T                         # (n, 3)
    w = projected[:, 2:3]
    w = np.where(np.abs(w) < 1e-9, 1e-9, w)                            # a point on the horizon
    metres = projected[:, :2] / w
    return metres / np.array([COURT_WIDTH_M, COURT_LENGTH_M])


def ground_scale(points: np.ndarray, image_to_court: np.ndarray) -> np.ndarray:
    """How many pixels one court metre spans, on the ground, at each image point. ``(n,)``

    The homography carries this for free: step one metre down-court from the point, project
    both ends back to the image, and measure the gap. Near the camera the answer is large,
    at the far baseline it is small, and that difference is exactly the perspective that
    :func:`court_size` has to divide out.

    Measured along the court's *length*, which is the axis the camera foreshortens, so the
    number is a scale factor rather than a physical height — it means something only when
    compared against another one from the same frame.
    """
    if len(points) == 0:
        return np.zeros(0, np.float64)

    court_to_image = np.linalg.inv(image_to_court)
    metres = to_court(points, image_to_court) * np.array([COURT_WIDTH_M, COURT_LENGTH_M])
    stepped = metres + np.array([0.0, 1.0])                            # one metre down-court
    homogeneous = np.hstack([stepped, np.ones((len(stepped), 1))])     # (n, 3)
    projected = homogeneous @ court_to_image.T
    w = projected[:, 2:3]
    w = np.where(np.abs(w) < 1e-9, 1e-9, w)
    return np.linalg.norm(projected[:, :2] / w - points, axis=1)


def court_size(bboxes: np.ndarray, feet: np.ndarray, image_to_court: np.ndarray) -> np.ndarray:
    """Each person's apparent height, in court metres instead of pixels. ``(n,)``

    This is the ranking signal — the one number that separates a player from the umpire.
    See the module docstring for why pixels cannot: they measure distance from the camera,
    and the umpire is closer to it than the far player is.

    Height rather than area, because a bounding box's *width* is mostly posture — a lunge
    or an outstretched racket arm doubles it — while its height tracks the person.
    Correcting the *area* for perspective instead (dividing by the metre squared) also
    sees through the umpire, but only reaches 98.4% on the test match against height's
    99.5%: the width contributes noise and no signal.
    """
    if len(bboxes) == 0:
        return np.zeros(0, np.float64)
    height = (bboxes[:, 3] - bboxes[:, 1]).astype(np.float64)
    return height / np.maximum(ground_scale(feet, image_to_court), 1e-6)


def candidate_margins(config: SelectConfig | None = None) -> tuple[float, float]:
    """The pre-filter's margins: never tighter than the selection they must feed."""
    config = config or SelectConfig()
    return (
        max(CANDIDATE_X_MARGIN, config.x_margin),
        max(CANDIDATE_Y_MARGIN, config.y_margin),
    )


def candidate_mask(
    bboxes: np.ndarray,
    image_to_court: np.ndarray,
    config: SelectConfig | None = None,
) -> np.ndarray:
    """Which detections are worth estimating a skeleton for, judged from the box alone.

    A broadcast frame holds nine to twelve people — the crowd, the umpire on their
    chair, line judges, coaches — and posing all of them costs about five times what
    posing the two players does. None of them can win the selection that follows, so
    this throws them out *before* RTMPose ever sees them, using the only ground point
    available at that stage: the bottom-centre of the bounding box.

    That point is cruder than the ankle midpoint the real selection uses, and a person
    dropped here is gone for good — there will be no skeleton to reconsider. So the
    band is much wider than the selection's own (:data:`CANDIDATE_X_MARGIN`,
    :data:`CANDIDATE_Y_MARGIN`), and it always contains it. It is a coarse "could this
    conceivably be a player?", not a decision.
    """
    if len(bboxes) == 0:
        return np.zeros(0, dtype=bool)

    x_margin, y_margin = candidate_margins(config)
    feet = np.stack(
        [(bboxes[:, 0] + bboxes[:, 2]) / 2.0, bboxes[:, 3]], axis=1
    ).astype(np.float64)
    court = to_court(feet, image_to_court)
    x, y = court[:, 0], court[:, 1]
    return (
        (x > -x_margin) & (x < 1 + x_margin)
        & (y > -y_margin) & (y < 1 + y_margin)
    )


def _halves(
    det: dict,
    image_to_court: np.ndarray,
    config: SelectConfig,
) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray]:
    """The in-court people, split into the two halves.

    Returns the ground points, ``{player: indices standing in that player's half}``, and
    each person's :func:`court_size`. Splitting at the net (normalized y = 0.5) is what
    guarantees the two players can never resolve to the same person.
    """
    feet = ground_points(det, config.min_ankle_score)
    court = to_court(feet, image_to_court)
    x, y = court[:, 0], court[:, 1]

    in_court = (
        (x > -config.x_margin) & (x < 1 + config.x_margin)
        & (y > -config.y_margin) & (y < 1 + config.y_margin)
    )
    halves = {
        "top": np.nonzero(in_court & (y < 0.5))[0],
        "bottom": np.nonzero(in_court & (y >= 0.5))[0],
    }
    size = court_size(det["bboxes"], feet, image_to_court)
    return feet, halves, size


def select_players(
    det: dict,
    image_to_court: np.ndarray,
    config: SelectConfig | None = None,
) -> tuple[int | None, int | None]:
    """Pick the two players from a single frame, with no memory of the previous one.

    Ranks the in-court candidates of each half by :func:`court_size` — apparent height
    measured against the court rather than the sensor, so that a player is compared with
    an official on equal terms instead of on how close each of them is to the camera. A
    standing athlete then genuinely out-measures a seated one; in pixels they do not.

    This is also what :class:`PlayerTracker` falls back on when it has no usable prior —
    at the start of a rally, or after a player has been missing long enough that where
    they were says nothing about where they are. Prefer the tracker for real work:
    continuity is a stronger signal than size, and size only has to be right often enough
    to hand the tracker a player to follow.
    """
    config = config or SelectConfig()
    if len(det["bboxes"]) == 0:
        return None, None

    _, halves, size = _halves(det, image_to_court, config)

    def best(ids: np.ndarray) -> int | None:
        return int(ids[np.argmax(size[ids])]) if len(ids) else None

    return best(halves["top"]), best(halves["bottom"])


class PlayerTracker:
    """Selection with a short memory: each player is the person nearest to where they were.

    The in-court test is geometry and stays exactly as it is; this only changes how the
    survivors are *ranked*. Continuity is a far stronger signal than size — a player
    moves a few pixels between frames (measured: median 3 px, 99th percentile 19 px),
    while a line judge who wanders into the widened court is a long way from where the
    player last stood.

    Two rules keep it honest:

    * **A gate, not a search.** If nobody is within ``max_step_px`` per frame of age of
      where the player was, the answer is None. It never widens its reach until it finds
      *someone* — the frames with no player visible are exactly the frames where the
      nearest remaining body is an official, and a confidently wrong skeleton is worse
      than an honest gap: nothing downstream can tell it apart from a real one.
    * **A stale prior is no prior.** After ``prior_max_age`` frames the memory is
      dropped and the next frame bootstraps from :func:`select_players` again, so a
      player who is genuinely lost is re-acquired rather than chased forever.

    One tracker per rally: call :meth:`reset` between segments, because the last frame of
    one rally says nothing about the first frame of the next.
    """

    def __init__(self, image_to_court: np.ndarray, config: SelectConfig | None = None) -> None:
        self.image_to_court = image_to_court
        self.config = config or SelectConfig()
        self._last: dict[str, np.ndarray | None] = {}
        self._age: dict[str, int] = {}
        self.reset()

    def reset(self) -> None:
        """Forget both priors."""
        self._last = {player: None for player in PLAYERS}
        self._age = {player: 0 for player in PLAYERS}

    def update(self, det: dict) -> tuple[int | None, int | None]:
        """Return ``(top_index, bottom_index)`` for this frame and remember the result."""
        config = self.config
        for player in PLAYERS:
            if self._last[player] is None:
                continue
            self._age[player] += 1
            if self._age[player] > config.prior_max_age:
                self._last[player], self._age[player] = None, 0

        if len(det["bboxes"]) == 0:
            return None, None

        feet, halves, size = _halves(det, self.image_to_court, config)
        picked = {player: self._pick(player, halves[player], feet, size) for player in PLAYERS}
        for player, index in picked.items():
            if index is not None:
                self._last[player], self._age[player] = feet[index].copy(), 0
        return picked["top"], picked["bottom"]

    def _pick(
        self,
        player: str,
        ids: np.ndarray,
        feet: np.ndarray,
        size: np.ndarray,
    ) -> int | None:
        if len(ids) == 0:
            return None

        prior = self._last[player]
        if prior is None:
            # Bootstrap: the biggest person in the half, measured on the court rather than
            # in pixels — in pixels the umpire wins, and the tracker would then spend the
            # whole rally faithfully following them. See court_size.
            return int(ids[np.argmax(size[ids])])

        distance = np.linalg.norm(feet[ids] - prior, axis=1)
        nearest = int(np.argmin(distance))
        # The gate grows with the age of the prior: a player missing for three frames has
        # had three frames in which to move.
        if distance[nearest] > self.config.max_step_px * max(self._age[player], 1):
            return None
        return int(ids[nearest])
