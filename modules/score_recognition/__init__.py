"""Score recognition stage: read the scoreboard for every rally segment.

See ``recognizer.recognize_scores`` for the pure pipeline and ``module`` for the
``BaseModule`` wrapper used by the analysis pipeline.
"""

from modules.score_recognition.module import ScoreRecognitionModule
from modules.score_recognition.recognizer import (
    ScoreRecognitionConfig,
    recognize_scores,
    score_segment,
)

__all__ = [
    "ScoreRecognitionModule",
    "ScoreRecognitionConfig",
    "recognize_scores",
    "score_segment",
]
