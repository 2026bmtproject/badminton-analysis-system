"""Selected optional vision using production records and explicit court trust."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from modules.artifacts import read_artifact
from modules.contracts import PIPELINE, artifact_path, PoseFrame, CourtCalibration, ShuttlePoint
from .streaming_json import iter_records

CourtPosition = Literal["top", "bottom"]


class CourtCalibrationPolicy(str, Enum):
    STRICT = "strict"
    ALLOW_UNCONFIRMED = "allow_unconfirmed"


@dataclass
class SelectedVisionStages:
    segment_index: int
    poses: list[PoseFrame] = field(default_factory=list)
    courts: list[CourtCalibration] = field(default_factory=list)
    points: list[ShuttlePoint] = field(default_factory=list)
    confirmed: bool = False
    detection_failed: bool = False
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        if any(r.segment_index != self.segment_index for r in [*self.poses, *self.points]):
            raise ValueError("selected vision contains another segment")


def read_selected_vision_stages(match_path, segment_index: int) -> SelectedVisionStages:
    vision = SelectedVisionStages(segment_index)
    for stage, target in (("pose", "poses"), ("court_detection", "courts"), ("shuttle_tracking", "points")):
        path = artifact_path(match_path, stage)
        if not path.is_file():
            vision.warnings.append(f"{stage}_stage_missing")
            continue
        spec = PIPELINE[stage]
        if stage == "pose":
            # Exhaust the iterator even after the segment ends: validate the tail.
            rows = [r for r in iter_records(path, spec.record_key) if r.get("segment_index") == segment_index]
        else:
            envelope = read_artifact(spec, path)
            rows = envelope[spec.record_key]
            if stage == "court_detection":
                vision.confirmed = envelope.get("confirmed") is True
                vision.detection_failed = envelope.get("detection_failed") is True
            else:
                rows = [r for r in rows if r.get("segment_index") == segment_index]
        setattr(vision, target, [spec.record_type(**r) for r in rows])
    return vision
