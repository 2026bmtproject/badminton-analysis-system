"""Pipeline artifacts -> :class:`SegmentFeatures`.

BST's reference implementation read four CSVs written by a pile of ad-hoc scripts. This
pipeline already produces all four things as artifacts, so this module is the seam
between them:

    segments.json  -> which frames each rally covers (and the fps)
    court.json     -> where the court is, for the players' court positions
    pose.json      -> both players' skeletons
    shuttle.json   -> the shuttle's trajectory

Three of the four have a convention that does not match what BST expects, and each is
the kind of mismatch that produces *plausible* numbers rather than a crash:

* **Court direction.** ``court.json`` stores court-metres -> image (that is what
  ``court_detection`` needs to draw with). BST wants image -> court, normalized to the
  unit square with y running from the far baseline to the near one. That is exactly what
  ``pose`` already does to pick the two players, so its two functions are reused rather
  than a third copy of the court maths being written — and reused specifically because
  getting the y direction backwards would swap Top and Bottom in every single
  prediction while still looking like a working model.
* **Missing means zero.** The artifacts mark an absent player or an invisible shuttle
  with ``None``; BST was trained to read a zero. Translating one to the other is this
  module's job, and it is why nothing here ever writes NaN.
* **Shuttle method.** ``shuttle.json`` holds *two* trajectories over the same frames
  (inpaint and viterbi). BST takes one; see :data:`DEFAULT_SHUTTLE_METHOD`.

Frame indices in the artifacts are absolute. ``SegmentFeatures`` are per-rally and local,
carrying ``start_frame`` so a caller can convert back.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np

from modules.artifacts import read_records, read_segments
from modules.common.bst.classes import L_ANKLE, NUM_KEYPOINTS, R_ANKLE
from modules.common.bst.features import SegmentFeatures, normalize_joints, normalize_shuttle
from modules.common.video import video_size
from modules.contracts import (
    PIPELINE,
    POSE_PLAYERS,
    SHUTTLE_METHODS,
    artifact_path,
    resolve_input_video,
)
from modules.pose.select import read_image_to_court, to_court

#: TrackNet's own output, gap-filled by InpaintNet — the closest thing to the trajectory
#: BST was trained on. ``viterbi`` is a different trade (smoother, more willing to invent
#: a plausible path), and swapping it in changes what the model sees, so it is a decision
#: a stage makes explicitly rather than a default that drifts.
DEFAULT_SHUTTLE_METHOD = "inpaint"


def read_fps(match_path: str | Path) -> float:
    """Just the fps from ``segments.json`` — see :func:`read_segments`."""
    return read_segments(match_path)[1]


def load_segment_features(
    match_path: str | Path,
    *,
    shuttle_method: str = DEFAULT_SHUTTLE_METHOD,
    video_size_px: tuple[int, int] | None = None,
) -> list[SegmentFeatures]:
    """Build one :class:`SegmentFeatures` per rally segment, in segment order.

    ``video_size_px`` is probed from the match video when omitted. It has to be the size
    the *other stages measured in*: pose keypoints and shuttle points are both in original
    video pixels, and normalizing the shuttle against a different resolution would shift
    the trajectory relative to the players without any of it looking wrong.
    """
    if shuttle_method not in SHUTTLE_METHODS:
        raise ValueError(
            f"unknown shuttle method {shuttle_method!r}; expected any of {SHUTTLE_METHODS}"
        )
    match_path = Path(match_path)
    width, height = video_size_px or video_size(str(resolve_input_video(match_path)))

    segments, _ = read_segments(match_path)
    image_to_court = read_image_to_court(match_path)

    poses = _pose_by_segment(read_records(PIPELINE["pose"], artifact_path(match_path, "pose")))
    shuttles = _shuttle_by_segment(
        read_records(PIPELINE["shuttle_tracking"], artifact_path(match_path, "shuttle_tracking")),
        shuttle_method,
    )

    features = []
    for index, segment in enumerate(segments):
        start = int(segment["start_frame"])
        n_frames = int(segment["end_frame"]) - start + 1
        joints, positions = _joints_and_positions(
            poses.get(index, {}), start, n_frames, image_to_court
        )
        features.append(
            SegmentFeatures(
                joints=joints,
                positions=positions,
                shuttle=_shuttle_array(shuttles.get(index, []), start, n_frames, width, height),
                start_frame=start,
            )
        )
    return features


# --------------------------------------------------------------------------- #
# pose.json
# --------------------------------------------------------------------------- #


def _pose_by_segment(records: list[dict]) -> dict[int, dict[str, list[dict]]]:
    """``{segment_index: {player: [record, ...]}}``, keeping only frames with a skeleton."""
    grouped: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        if record.get("keypoints") is None or record.get("bbox") is None:
            continue                      # that player was not found in that frame
        grouped[int(record["segment_index"])][record["player"]].append(record)
    return grouped


def _joints_and_positions(
    by_player: dict[str, list[dict]],
    start_frame: int,
    n_frames: int,
    image_to_court: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """One segment's ``(joints (N, 2, 17, 2), positions (N, 2, 2))``.

    Slot 0 is ``top``, slot 1 is ``bottom`` — the order of ``contracts.POSE_PLAYERS``, and
    the order BST's two-player streams were trained in. A frame where a player was not
    found stays all zeros, which is how the model was taught to read "not there".
    """
    joints = np.zeros((n_frames, 2, NUM_KEYPOINTS, 2), dtype=np.float32)
    positions = np.zeros((n_frames, 2, 2), dtype=np.float32)

    for slot, player in enumerate(POSE_PLAYERS):
        records = by_player.get(player, [])
        rows = [
            (int(r["frame"]) - start_frame, r) for r in records
            if 0 <= int(r["frame"]) - start_frame < n_frames
        ]
        if not rows:
            continue

        index = np.array([i for i, _ in rows])
        keypoints = np.nan_to_num(
            np.array([r["keypoints"] for _, r in rows], dtype=np.float64)[:, :, :2], nan=0.0
        )                                                          # (m, 17, 2) — drop scores
        bboxes = np.array([r["bbox"] for _, r in rows], dtype=np.float64)

        joints[index, slot] = normalize_joints(keypoints, bboxes, center_align=True)
        feet, standing = _ground_points(keypoints)
        if standing.any():
            positions[index[standing], slot] = to_court(feet[standing], image_to_court)
    return joints, positions


def _ground_points(keypoints: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Where each player is standing: the mean of whichever ankles are present.

    Returns the ``(m, 2)`` image points and an ``(m,)`` mask of who has a usable one.
    Deliberately *not* ``pose.select.ground_points``, which falls back to the bottom of
    the bounding box when the ankles are unreliable: BST's training data had no such
    fallback, and a court position invented from a bbox is a different input distribution
    than the one the weights were fitted to. Here a player with no ankles simply has no
    position, which is a zero — a value the model saw plenty of during training.
    """
    ankles = keypoints[:, [L_ANKLE, R_ANKLE], :]                   # (m, 2, 2)
    present = (ankles[:, :, 0] != 0.0) & (ankles[:, :, 1] != 0.0)  # (m, 2)
    count = present.sum(axis=1)
    total = (ankles * present[:, :, None]).sum(axis=1)
    return total / np.maximum(count, 1)[:, None], count > 0


# --------------------------------------------------------------------------- #
# shuttle.json
# --------------------------------------------------------------------------- #


def _shuttle_by_segment(records: list[dict], method: str) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    found = False
    for record in records:
        if record.get("method") != method:
            continue
        found = True
        if record.get("visible") and record.get("x") is not None:
            grouped[int(record["segment_index"])].append(record)
    if not found:
        raise RuntimeError(
            f"shuttle_tracking output has no {method!r} points — it was run with a "
            "different method"
        )
    return grouped


def _shuttle_array(
    records: list[dict], start_frame: int, n_frames: int, width: int, height: int
) -> np.ndarray:
    """One segment's ``(N, 2)`` normalized shuttle track; invisible frames are (0, 0)."""
    coords = np.zeros((n_frames, 2), dtype=np.float64)
    for record in records:
        local = int(record["frame"]) - start_frame
        if 0 <= local < n_frames:
            coords[local] = (float(record["x"]), float(record["y"]))
    return normalize_shuttle(coords, width, height)
