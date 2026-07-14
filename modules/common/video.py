"""Streaming frame access shared by stages that walk a segment end to end.

``frame_composite.extract_frames_in_range`` *samples* a handful of frames out of a
range; this reads **every** frame of the range, one at a time. A stage whose model
looks at a single frame (``pose``) can then run at constant memory however long the
rally is, instead of materializing the whole segment like ``shuttle_tracking`` has to
for its temporal window.
"""

from __future__ import annotations

from typing import Iterator

import cv2
import numpy as np


def iter_segment_frames(
    video_path: str,
    start_frame: int,
    end_frame: int,
) -> Iterator[tuple[int, np.ndarray]]:
    """Yield ``(absolute_frame_index, bgr_frame)`` for the inclusive range.

    Seeks once and then reads sequentially — seeking per frame is far slower and, on
    some codecs, lands on the wrong frame. Stops early and without complaint if the
    video ends before ``end_frame``: a truncated file yields fewer frames rather than
    raising, and the caller sees that in the count it got.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_frame))
        for index in range(int(start_frame), int(end_frame) + 1):
            ok, frame = cap.read()
            if not ok:
                return
            yield index, frame
    finally:
        cap.release()


def video_size(video_path: str) -> tuple[int, int]:
    """The video's ``(width, height)`` in pixels."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    try:
        return (
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
    finally:
        cap.release()
