"""Pose (RTMPose): rally segments -> the two players' COCO-17 skeletons per frame.

Top-down and two-stage: YOLOX finds every person in the frame, RTMPose re-estimates
each one's skeleton on an enlarged crop (``estimator``), and the court homography from
``court_detection`` decides which two of those people are the players (``select``).

Everyone who could plausibly be a player is cached under ``cache/pose/`` before the
selection runs, so the margins that decide "is this person on the court" — the part
that needs tuning against real footage, especially for airborne players — can be re-run
without repeating the GPU pass. ``overlay`` draws that decision onto real frames so it
can be checked by eye.
"""

from modules.pose.module import PoseConfig, PoseModule

__all__ = ["PoseConfig", "PoseModule"]
