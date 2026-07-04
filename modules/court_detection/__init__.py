"""Court detection stage: locate the court boundary once per match.

The longest rally segments are median-composited into one occlusion-free court
image and passed to a robust, colour-agnostic detector (see ``detector``). An
optional interactive step (see ``interactive``) lets a user fine-tune the four
corners before the ``court.json`` artifact is written.
"""

from modules.court_detection.module import (
    CourtDetectionConfig,
    CourtDetectionModule,
)

__all__ = [
    "CourtDetectionModule",
    "CourtDetectionConfig",
]
