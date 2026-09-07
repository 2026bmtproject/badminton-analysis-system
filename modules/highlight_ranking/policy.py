"""Transparent audio_additive_v1 heuristic; no inference or media dependencies."""
import math

from modules.contracts import AudioSegmentSignals, HighlightScore

POLICY_NAME = "audio_additive_v1"
CONFIDENCE_WEIGHT = 0.5
INTENSITY_WEIGHT = 0.5


def policy_metadata() -> dict:
    """Return fresh, machine-readable parameters for the fixed v1 policy."""
    return {
        "name": POLICY_NAME,
        "confidence_weight": CONFIDENCE_WEIGHT,
        "intensity_weight": INTENSITY_WEIGHT,
        "null_intensity": "zero_contribution",
        "score_range": [0.0, 1.0],
        "ranking_order": ["score_desc", "cheer_confidence_desc", "segment_index_asc"],
    }


def score_audio_signal(signal: AudioSegmentSignals) -> HighlightScore:
    """Validate and score one signal, retaining null versus zero in the input.

    The score is partially match-relative, not a probability or globally
    calibrated value. Support count validates applicability; it adds no score.
    """
    context = f"audio signal segment_index={signal.segment_index!r}"
    for name in ("segment_index", "n_cheer_windows"):
        value = getattr(signal, name)
        if type(value) is not int or value < 0:
            raise ValueError(f"{context}: {name} must be a non-negative integer")
    for name in ("cheer_confidence", "cheer_intensity"):
        value = getattr(signal, name)
        if name == "cheer_intensity" and value is None:
            continue
        if (type(value) not in (int, float) or not 0 <= value <= 1
                or not math.isfinite(value)):
            raise ValueError(f"{context}: {name} must be a finite number in [0, 1]")
    intensity = signal.cheer_intensity
    if (intensity is None) != (signal.n_cheer_windows == 0):
        raise ValueError(
            f"{context}: null cheer_intensity requires n_cheer_windows=0; "
            "non-null cheer_intensity requires n_cheer_windows>0"
        )
    effective_intensity = 0.0 if intensity is None else intensity
    return HighlightScore(
        signal.segment_index,
        CONFIDENCE_WEIGHT * signal.cheer_confidence + INTENSITY_WEIGHT * effective_intensity,
    )


def score_audio_signals(signals: list[AudioSegmentSignals]) -> list[HighlightScore]:
    """Score every input, returning segment-index order, not ranked order.

    Consumers rank by full-precision score descending, source cheer_confidence
    descending, then segment_index ascending. No rounding or epsilon ties.
    Indices need not be contiguous: their mapping to segments belongs upstream.
    """
    if not signals:
        raise ValueError("audio signals must not be empty; complete audio_highlight first")
    seen: set[int] = set()
    scores = []
    for signal in signals:
        score = score_audio_signal(signal)
        if score.segment_index in seen:
            raise ValueError(f"duplicate segment_index={score.segment_index} in audio signals")
        seen.add(score.segment_index)
        scores.append(score)
    return sorted(scores, key=lambda score: score.segment_index)
