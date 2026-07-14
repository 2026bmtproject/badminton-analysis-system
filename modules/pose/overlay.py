"""Draw the selection decision onto a frame, so it can be checked by eye.

The in-court test is geometry and can be reasoned about; the ranking that follows it,
and the size of the margins, are heuristics that can only really be judged by looking
at frames — above all the ones this stage is most likely to get wrong: a player mid-jump,
whose feet project past the baseline (see :mod:`modules.pose.select`).

Rendered per person: the ground point used, and where it landed in court coordinates.
The two chosen players are drawn with their skeleton; everyone else is drawn dimmed, so
a line judge being picked up is immediately obvious.
"""

from __future__ import annotations

import cv2
import numpy as np

from modules.pose.select import SelectConfig, ground_points, to_court

#: COCO-17 bones.
SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]

COLORS = {
    "top": (0, 200, 255),       # amber
    "bottom": (0, 255, 120),    # green
    "other": (120, 120, 120),   # grey
}
KEYPOINT_SCORE = 0.3


def draw(
    frame: np.ndarray,
    det: dict,
    image_to_court: np.ndarray,
    picked: tuple[int | None, int | None],
    config: SelectConfig | None = None,
) -> np.ndarray:
    """Return a copy of ``frame`` annotated with every person and the two chosen ones.

    ``picked`` is passed in rather than recomputed because the real selection has memory
    (:class:`~modules.pose.select.PlayerTracker`) — re-deriving it from this frame alone
    would draw a decision the stage never made, which is worse than useless in a picture
    whose whole job is to show what the stage did.
    """
    config = config or SelectConfig()
    canvas = frame.copy()

    top, bottom = picked
    labels = {top: "top", bottom: "bottom"}
    labels.pop(None, None)

    feet = ground_points(det, config.min_ankle_score)
    court = to_court(feet, image_to_court)

    for person in range(len(det["bboxes"])):
        label = labels.get(person, "other")
        color = COLORS[label]
        chosen = label != "other"

        x1, y1, x2, y2 = (int(v) for v in det["bboxes"][person])
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2 if chosen else 1)

        # Where this person was judged to be standing, in court coordinates. Values
        # outside 0..1 are outside the court -- which is exactly what the margins allow.
        cx, cy = court[person]
        text = f"{label} ({cx:+.2f}, {cy:+.2f})"
        cv2.putText(canvas, text, (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

        foot = tuple(int(v) for v in feet[person])
        cv2.drawMarker(canvas, foot, color, cv2.MARKER_CROSS, 14, 2)

        if not chosen:
            continue
        kps, scores = det["kps"][person], det["scores"][person]
        for a, b in SKELETON:
            if scores[a] < KEYPOINT_SCORE or scores[b] < KEYPOINT_SCORE:
                continue
            cv2.line(canvas, tuple(kps[a].astype(int)), tuple(kps[b].astype(int)), color, 2)
        for joint, score in zip(kps, scores):
            if score >= KEYPOINT_SCORE:
                cv2.circle(canvas, tuple(joint.astype(int)), 3, color, -1)

    return canvas
