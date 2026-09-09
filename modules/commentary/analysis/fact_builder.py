"""Selected-rally joins over authoritative production record types."""

import math
from collections.abc import Sequence

from modules.contracts import Segment, HitEvent, StrokeLabel, RallyScore as StageScore, HighlightScore
from modules.commentary.identity import CourtPositionToPlayer
from modules.commentary.schemas import RallyFact, RallyFactEvent, RallyScore


def build_rally_fact(
    *,
    segments: Sequence[Segment],
    fps: float,
    events: Sequence[HitEvent],
    strokes: Sequence[StrokeLabel],
    scores: Sequence[StageScore],
    segment_index: int,
    court_position_to_player: CourtPositionToPlayer | None,
    highlights: Sequence[HighlightScore] = (),
) -> RallyFact:
    """Join selected hits without changing source indices or guessing identities."""
    if type(segment_index) is not int or not 0 <= segment_index < len(segments):
        raise ValueError("segment_index does not exist")
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be finite and positive")
    segment = segments[segment_index]
    if (type(segment.start_frame) is not int or type(segment.end_frame) is not int
            or not 0 <= segment.start_frame <= segment.end_frame):
        raise ValueError("invalid segment frame range")
    for frame, second in ((segment.start_frame, segment.start_sec), (segment.end_frame, segment.end_sec)):
        if not math.isfinite(second) or abs(frame / fps - second) > 0.001 + 1e-9:
            raise ValueError("segment frame/time disagreement")
    if any(type(e.frame) is not int or e.frame < 0 for e in events):
        raise ValueError("invalid event frame")
    selected = {i for i, e in enumerate(events) if segment.start_frame <= e.frame <= segment.end_frame}
    indexed: dict[int, StrokeLabel] = {}
    for stroke in strokes:
        index = stroke.event_index
        if type(index) is not int or not 0 <= index < len(events):
            raise ValueError("invalid stroke event_index")
        if index in indexed:
            raise ValueError("duplicate stroke event_index")
        indexed[index] = stroke
        if index in selected or stroke.segment_index == segment_index:
            if type(stroke.frame) is not int or stroke.frame != events[index].frame:
                raise ValueError("event/stroke frame mismatch")
            if type(stroke.segment_index) is not int or stroke.segment_index != segment_index or index not in selected:
                raise ValueError("stroke segment mismatch")
    def one(rows, name):
        rows = [r for r in rows if r.segment_index == segment_index]
        if len(rows) > 1:
            raise ValueError(f"duplicate {name} for selected segment")
        return rows[0] if rows else None
    score = one(scores, "score")
    if score is not None and score.sub_scores is not None and len(score.sub_scores) > 1:
        raise ValueError("commentary represents one rally per segment; cannot safely identify a sub-rally in multiple recovered rallies yet")
    highlight = one(highlights, "highlight")
    result: list[RallyFactEvent] = []
    for index in sorted(selected, key=lambda i: (events[i].frame, i)):
        stroke = indexed.get(index)
        player = None
        if stroke is not None and stroke.player is not None:
            if court_position_to_player is None:
                raise ValueError("explicit court_position_to_player mapping is required")
            player = court_position_to_player.resolve(stroke.player)
        result.append(RallyFactEvent(event_index=index, frame=events[index].frame,
            time_sec=events[index].frame / fps, player=player,
            stroke_type=stroke.stroke_type if stroke is not None else None,
            stroke_confidence=stroke.confidence if stroke is not None else None))
    return RallyFact(segment_index=segment_index, game_index=score.game_index if score is not None else None,
        start_sec=segment.start_sec, end_sec=segment.end_sec, duration_sec=segment.duration_sec,
        score=RallyScore(a=score.score_a if score is not None else None, b=score.score_b if score is not None else None),
        server=score.server if score is not None else None, events=result, rally_length=len(result),
        highlight_score=highlight.score if highlight is not None else None)
