"""Pick the two players out of everyone the detector found.

Each detected person is reduced to a ground point (their feet), projected through the
court homography into court metres; only people within a margin of the court survive.
The survivor in the far half is ``top``, the one in the near half is ``bottom``.

The court is enlarged asymmetrically (:attr:`SelectConfig.x_margin` / ``y_margin``)
because the homography only holds on the ground plane — a player mid-smash has their
ankles in the air, so back-projecting pushes them past the far baseline. Margins are
the acceptance test; within each half, candidates are then ranked and only the best is
kept (see :func:`select_players`).

Ranking uses :func:`court_size` — apparent height in court metres — instead of pixel
size, because pixel size just reflects distance from the camera: the umpire, seated
closer than the far player, reads as bigger in pixels despite not being a player.
Dividing by metres-per-pixel at the ground point (:func:`ground_scale`) removes that
bias.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from modules.artifacts import read_artifact
from modules.contracts import PIPELINE, POSE_PLAYERS, artifact_path
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
#: :func:`candidate_mask`. Must stay looser than the selection margins below — the
#: pre-filter judges from the bounding box while selection judges from the ankles, and
#: a candidate dropped here has no skeleton left to reconsider.
CANDIDATE_X_MARGIN = 0.35
CANDIDATE_Y_MARGIN = 0.45


@dataclass
class SelectConfig:
    """Knobs for turning detections into two players.

    * ``x_margin`` 0.25 (~1.5 m outside a sideline) covers lunges (measured up to
      1.74 m out). ``y_margin`` 0.25 (~3.4 m past a baseline) covers the jump
      artifact described in the module docstring.
    * ``min_ankle_score`` — below this, ankles are too unreliable and the bounding
      box's bottom edge is used as the ground point instead.
    * ``min_bootstrap_size`` — floor on :func:`court_size`, applied only when
      (re-)acquiring a player from scratch, not during continuity tracking. Measured
      4.0: standing players score 5.5-9, a deep player dips to 3.9-4.4. Does **not**
      keep the umpire out — they score 4.5-4.9, above a deep player — that's what
      ``build_static_anchors`` is for.
    * ``anchor_grid`` / ``anchor_min_occupancy`` / ``anchor_max_pxstd`` /
      ``anchor_radius`` — parameterise static-distractor exclusion; see
      :func:`build_static_anchors`.
    * ``max_step_px`` / ``prior_max_age`` — gate :class:`PlayerTracker`.
    """

    x_margin: float = 0.25           # fraction of court width, outside each sideline
    y_margin: float = 0.25           # fraction of court length, outside each baseline
    min_ankle_score: float = 0.3
    min_bootstrap_size: float = 4.0  # court_size floor when acquiring a player from scratch
    max_step_px: float = 120.0       # how far a player may move per frame of prior age
    prior_max_age: int = 5           # frames after which a prior is stale and dropped

    # Static-distractor exclusion (see build_static_anchors). A fixture is a ground
    # point occupied in a large fraction of the match's frames while barely moving —
    # the umpire and line judges are exactly that; a player never is.
    anchor_grid: float = 25.0        # px cell for the occupancy histogram
    anchor_min_occupancy: float = 0.25  # a cell this fraction of frames or more is a fixture
    anchor_max_pxstd: float = 6.0    # ...provided its points barely scatter (players jitter more)
    anchor_radius: float = 30.0      # a candidate this close to a fixture is refused


def court_from_image(homography) -> np.ndarray:
    """Invert ``court.json``'s homography into the image -> court-metres direction."""
    matrix = np.asarray(homography, dtype=np.float64)
    if matrix.shape != (3, 3):
        raise ValueError(f"homography must be 3x3, got {matrix.shape}")
    try:
        return np.linalg.inv(matrix)
    except np.linalg.LinAlgError as e:
        raise ValueError("court homography is singular and cannot be inverted") from e


def read_image_to_court(match_path: str | Path) -> np.ndarray:
    """The image -> court-metres matrix, read out of ``court.json`` and inverted."""
    spec = PIPELINE["court_detection"]
    envelope = read_artifact(spec, artifact_path(match_path, spec.name))
    courts = envelope[spec.record_key]
    if not courts:
        raise RuntimeError("no court in court_detection output")
    return court_from_image(courts[0]["homography"])


def ground_points(det: dict, min_ankle_score: float) -> np.ndarray:
    """The ``(n, 2)`` image point each person is standing on.

    Ankle midpoint normally; falls back to the bounding box's bottom-centre when
    neither ankle is confidently placed (occlusion, frame edge, a lunge).
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
    """Pixels spanned by one court metre on the ground, at each image point. ``(n,)``

    Steps one metre down-court from the point and measures the image-space gap via the
    homography. Larger near the camera, smaller at the far baseline — that difference
    is exactly the perspective :func:`court_size` divides out.
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

    Height rather than area: a bounding box's width is mostly posture (a lunge or
    outstretched racket arm doubles it) and adds noise rather than signal.
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

    Cheap pre-filter so RTMPose only runs on people who could plausibly be a player,
    using the bounding-box bottom-centre — cruder than the ankle midpoint the real
    selection uses. A person dropped here is gone for good, so the band is much wider
    than the selection's own (:data:`CANDIDATE_X_MARGIN`, :data:`CANDIDATE_Y_MARGIN`)
    and always contains it.
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


def build_static_anchors(
    detections: "Iterable[dict]",
    config: SelectConfig | None = None,
) -> np.ndarray:
    """The fixed ground points a whole match keeps a body standing on. ``(m, 2)`` image px.

    Finds people who don't move — the umpire beside the net, any line judge inside the
    sideline margin — since size alone can't separate them from a deep player (see
    :attr:`SelectConfig.min_bootstrap_size`), but motion can: a player sweeps across
    the court, an official's feet land on the same pixel all match long.

    Quantises every candidate's ground point into a coarse grid over the whole match
    and returns the centre of each cell that is occupied in at least
    ``anchor_min_occupancy`` of frames and tight (scatter under ``anchor_max_pxstd``
    px). :class:`PlayerTracker` refuses any candidate within ``anchor_radius`` of one.

    Self-calibrating per match and camera. Pass the same detections the selection will
    run on (the cache is exactly this).
    """
    config = config or SelectConfig()
    grid = config.anchor_grid
    cell_frames: dict[tuple[int, int], int] = {}
    cell_points: dict[tuple[int, int], list[np.ndarray]] = {}
    n_frames = 0
    for det in detections:
        n_frames += 1
        if len(det["bboxes"]) == 0:
            continue
        feet = ground_points(det, config.min_ankle_score)
        seen: set[tuple[int, int]] = set()
        for point in feet:
            cell = (int(point[0] // grid), int(point[1] // grid))
            cell_points.setdefault(cell, []).append(point)
            if cell not in seen:                       # count a cell once per frame
                cell_frames[cell] = cell_frames.get(cell, 0) + 1
                seen.add(cell)

    if n_frames == 0:
        return np.zeros((0, 2), np.float64)

    anchors: list[np.ndarray] = []
    for cell, frames in cell_frames.items():
        if frames / n_frames < config.anchor_min_occupancy:
            continue
        points = np.array(cell_points[cell])
        if points[:, 0].std() > config.anchor_max_pxstd or points[:, 1].std() > config.anchor_max_pxstd:
            continue                                   # it moves — a player passing through
        anchors.append(points.mean(axis=0))
    return np.array(anchors, np.float64) if anchors else np.zeros((0, 2), np.float64)


def _anchor_block(feet: np.ndarray, anchors: np.ndarray | None, radius: float) -> np.ndarray:
    """Bool per detection: True where its ground point sits on a static anchor."""
    blocked = np.zeros(len(feet), dtype=bool)
    if anchors is None or len(anchors) == 0 or len(feet) == 0:
        return blocked
    for anchor in anchors:
        blocked |= np.linalg.norm(feet - anchor, axis=1) < radius
    return blocked


def select_players(
    det: dict,
    image_to_court: np.ndarray,
    config: SelectConfig | None = None,
    anchors: np.ndarray | None = None,
) -> tuple[int | None, int | None]:
    """Pick the two players from a single frame, with no memory of the previous one.

    Ranks the in-court candidates of each half by :func:`court_size` — apparent height
    measured against the court rather than the sensor, so a player is compared with an
    official on equal terms instead of on how close each is to the camera.

    Also what :class:`PlayerTracker` falls back on when it has no usable prior. Prefer
    the tracker for real work: continuity is a stronger signal than size.
    """
    config = config or SelectConfig()
    if len(det["bboxes"]) == 0:
        return None, None

    feet, halves, size = _halves(det, image_to_court, config)
    blocked = _anchor_block(feet, anchors, config.anchor_radius)

    def best(ids: np.ndarray) -> int | None:
        # Drop anyone standing on a static anchor (umpire, line judge).
        ids = ids[~blocked[ids]]
        if len(ids) == 0:
            return None
        # Bootstrap pick: refuse a candidate too small to be a standing player.
        winner = int(ids[np.argmax(size[ids])])
        return winner if size[winner] >= config.min_bootstrap_size else None

    return best(halves["top"]), best(halves["bottom"])


class PlayerTracker:
    """Selection with a short memory: each player is the person nearest to where they were.

    The in-court test is unchanged; this only changes how survivors are *ranked*.
    Continuity is a far stronger signal than size — a player moves a few pixels
    between frames (measured: median 3 px, 99th percentile 19 px).

    Three rules keep it honest:

    * **A gate, not a search.** If nobody is within ``max_step_px`` per frame of age of
      where the player was, the answer is None rather than the nearest wrong body.
    * **A stale prior is no prior.** After ``prior_max_age`` frames the memory is
      dropped and the next frame bootstraps from :func:`select_players` again.
    * **No fixtures.** Candidates standing on a static anchor (``anchors``, from
      :func:`build_static_anchors`) are removed before either rule runs. This is what
      actually keeps the umpire out: the gate alone doesn't, since a missing player's
      widening gate eventually reaches the motionless umpire and locks on for good.

    One tracker per rally: call :meth:`reset` between segments, because the last frame
    of one rally says nothing about the first frame of the next.
    """

    def __init__(
        self,
        image_to_court: np.ndarray,
        config: SelectConfig | None = None,
        anchors: np.ndarray | None = None,
    ) -> None:
        self.image_to_court = image_to_court
        self.config = config or SelectConfig()
        self.anchors = anchors            # static distractors; see build_static_anchors
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
        blocked = _anchor_block(feet, self.anchors, config.anchor_radius)
        picked = {
            player: self._pick(player, halves[player], feet, size, blocked)
            for player in PLAYERS
        }
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
        blocked: np.ndarray,
    ) -> int | None:
        if len(ids) == 0:
            return None

        # A static anchor is never a player; removing it here is what breaks the
        # umpire lock (see class docstring).
        ids = ids[~blocked[ids]]
        if len(ids) == 0:
            return None

        prior = self._last[player]
        if prior is None:
            # Bootstrap: biggest person in the half by court_size, refused if too
            # small to be a standing player (SelectConfig.min_bootstrap_size).
            winner = int(ids[np.argmax(size[ids])])
            return winner if size[winner] >= self.config.min_bootstrap_size else None

        distance = np.linalg.norm(feet[ids] - prior, axis=1)
        nearest = int(np.argmin(distance))
        # The gate grows with the age of the prior: a player missing for three frames
        # has had three frames in which to move.
        if distance[nearest] > self.config.max_step_px * max(self._age[player], 1):
            return None
        return int(ids[nearest])
