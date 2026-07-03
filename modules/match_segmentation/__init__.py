"""Match segmentation stage: split a match video into candidate rally segments.

See ``segmenter.segment_video`` for the pure pipeline and ``module``
for the ``BaseModule`` wrapper used by the analysis pipeline.
"""

from modules.match_segmentation.module import MatchSegmentationModule
from modules.match_segmentation.segmenter import (
    SegmentationConfig,
    SegmentationResult,
    segment_video,
)

__all__ = [
    "MatchSegmentationModule",
    "SegmentationConfig",
    "SegmentationResult",
    "segment_video",
]
