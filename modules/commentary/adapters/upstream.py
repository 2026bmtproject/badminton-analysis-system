"""Read production stage artifacts directly; no historical input wrappers."""

from dataclasses import dataclass, field
from pathlib import Path

from modules.artifacts import read_artifact
from modules.contracts import PIPELINE, artifact_path, Segment, HitEvent, StrokeLabel, RallyScore, HighlightScore
from modules.commentary.identity import CourtPositionToPlayer
from modules.commentary.schemas import RallyFact
from modules.commentary.analysis.fact_builder import build_rally_fact
from .vision import SelectedVisionStages, CourtCalibrationPolicy, read_selected_vision_stages


@dataclass
class UpstreamStageData:
    segments: list[Segment]
    fps: float
    events: list[HitEvent]
    strokes: list[StrokeLabel]
    scores: list[RallyScore]
    highlights: list[HighlightScore] = field(default_factory=list)
    # Production stroke_classification envelope metadata, not a StrokeLabel field.
    shuttle_method: str | None = None
    vision: SelectedVisionStages | None = None
    court_calibration_policy: CourtCalibrationPolicy = CourtCalibrationPolicy.STRICT


def build_rally_fact_from_stages(*, stages: UpstreamStageData, segment_index: int,
                               court_position_to_player: CourtPositionToPlayer | None) -> RallyFact:
    return build_rally_fact(segments=stages.segments, fps=stages.fps,
        events=stages.events, strokes=stages.strokes, scores=stages.scores,
        highlights=stages.highlights, segment_index=segment_index,
        court_position_to_player=court_position_to_player)


def read_commentary_inputs(match_path: str | Path, segment_index: int,
                           court_position_to_player: CourtPositionToPlayer | None,
                           court_calibration_policy: CourtCalibrationPolicy = CourtCalibrationPolicy.STRICT
                           ) -> UpstreamStageData:
    data = {}
    metadata = {}
    for stage, target in (("match_segmentation", "segments"), ("event_detection", "events"),
                          ("stroke_classification", "strokes"), ("score_recognition", "scores"),
                          ("highlight_ranking", "highlights")):
        spec = PIPELINE[stage]
        path = artifact_path(match_path, stage)
        if stage == "highlight_ranking" and not path.is_file():
            continue
        envelope = read_artifact(spec, path)
        data[target] = [spec.record_type(**r) for r in envelope[spec.record_key]]
        if stage == "match_segmentation":
            metadata["fps"] = envelope.get("fps")
        elif stage == "stroke_classification":
            metadata["shuttle_method"] = envelope.get("shuttle_method")
    stages = UpstreamStageData(**data, **metadata,
        court_calibration_policy=CourtCalibrationPolicy(court_calibration_policy))
    build_rally_fact_from_stages(stages=stages, segment_index=segment_index,
        court_position_to_player=court_position_to_player)
    stages.vision = read_selected_vision_stages(match_path, segment_index)
    return stages
