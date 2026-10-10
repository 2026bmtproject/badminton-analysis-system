"""Validated artifact envelopes and the versioned Review export contract.

Stage record dataclasses in :mod:`modules.contracts` remain the producer contract.
These models cover envelope metadata and nullable legacy fields consumed by Review.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REVIEW_EXPORT_SCHEMA_VERSION = "review-export-v1"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SegmentRecord(ContractModel):
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    duration_sec: float = Field(ge=0)


class SegmentsArtifact(ContractModel):
    fps: float = Field(gt=0)
    segments: list[SegmentRecord] = Field(min_length=1)


class ScoreRecord(ContractModel):
    segment_index: int = Field(ge=0)
    score_a: int | None = Field(default=None, ge=0)
    score_b: int | None = Field(default=None, ge=0)
    server: Literal["a", "b"] | None = None
    game_index: int | None = Field(default=None, ge=0)
    sub_scores: list[tuple[int, int]] | None = None
    split_secs: list[float] | None = None


class ScoresArtifact(ContractModel):
    rallies: list[ScoreRecord]
    attempts: list[dict[str, Any]] = Field(default_factory=list)


class EventRecord(ContractModel):
    frame: int = Field(ge=0)


class EventsArtifact(ContractModel):
    events: list[EventRecord]


class StrokeRecord(ContractModel):
    event_index: int = Field(ge=0)
    frame: int = Field(ge=0)
    segment_index: int = Field(ge=0)
    player: Literal["top", "bottom"] | None
    stroke_type: str
    confidence: float = Field(ge=0, le=1)


class StrokesArtifact(ContractModel):
    strokes: list[StrokeRecord]


class AudioSignalRecord(ContractModel):
    segment_index: int = Field(ge=0)
    cheer_confidence: float = Field(ge=0, le=1)
    cheer_intensity: float | None = Field(default=None, ge=0, le=1)
    n_cheer_windows: int = Field(ge=0)


class AudioWindowRecord(ContractModel):
    segment_index: int = Field(ge=0)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    cheer_probability: float = Field(ge=0, le=1)


class AudioArtifact(ContractModel):
    signals: list[AudioSignalRecord]
    windows: list[AudioWindowRecord] | None = None


class HighlightRecord(ContractModel):
    segment_index: int = Field(ge=0)
    score: float = Field(ge=0, le=1)


class HighlightsArtifact(ContractModel):
    highlights: list[HighlightRecord]


class IdentityEpoch(ContractModel):
    first_segment: int = Field(ge=0)
    last_segment: int = Field(ge=0)
    game_index: int = Field(ge=0)
    top: Literal["a", "b"]
    bottom: Literal["a", "b"]


class IdentityArtifact(ContractModel):
    epochs: list[IdentityEpoch]


class CourtRecord(ContractModel):
    homography: list[list[float]]
    segment_index: int | None = Field(default=None, ge=0)


class CourtArtifact(ContractModel):
    courts: list[CourtRecord]


class PoseRecord(ContractModel):
    frame: int = Field(ge=0)
    segment_index: int = Field(ge=0)
    player: Literal["top", "bottom"]
    keypoints: list[list[float]] | None
    bbox: list[float] | None = None


class PoseArtifact(ContractModel):
    frames: list[PoseRecord]


class ShuttleRecord(ContractModel):
    frame: int = Field(ge=0)
    segment_index: int = Field(ge=0)
    method: str = Field(min_length=1)
    x: float | None
    y: float | None
    visible: bool


class ShuttleArtifact(ContractModel):
    points: list[ShuttleRecord]


class CommentarySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    source_fact_ids: list[str] = Field(min_length=1)


class CommentaryEvent(CommentarySummary):
    stroke_index: int = Field(ge=0)
    frame: int = Field(ge=0)
    time_sec: float = Field(ge=0)
    player: Literal["a", "b"]


class CommentaryRally(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_index: int = Field(ge=0)
    events: list[CommentaryEvent]
    summary: CommentarySummary | None


class UnsupportedCommentary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_index: int = Field(ge=0)
    reason: str = Field(min_length=1)


class CommentaryArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["commentary-rallies-v1"]
    rallies: list[CommentaryRally]
    unsupported_segments: list[UnsupportedCommentary]
    diagnostics: list[dict[str, Any]]
    runtime: dict[str, Any]


class SegmentCommentaryArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["commentary-segment-v1"]
    segment_index: int = Field(ge=0)
    rally: CommentaryRally
    identity: dict[str, Any]
    tactical: dict[str, Any]
    commentary: dict[str, Any]
    runtime: dict[str, Any]


ArtifactStatus = Literal["available", "missing", "error", "stale", "unknown"]


class ReviewStageState(BaseModel):
    status: ArtifactStatus
    usable: bool
    message: str | None = Field(default=None, exclude_if=lambda value: value is None)
    fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)


class ReviewSource(BaseModel):
    matchId: str
    importedAt: str
    backendBaseCommit: str | None = Field(default=None, exclude_if=lambda value: value is None)
    fingerprints: dict[str, str]
    limitations: list[str]


class ReviewEvidence(BaseModel):
    id: str
    text: str
    eventIndex: int | None
    time: float | None


class ReviewCommentarySummary(BaseModel):
    segmentIndex: int = Field(ge=0)
    text: str = Field(min_length=1)
    sourceFactIds: list[str] = Field(min_length=1)
    evidence: list[ReviewEvidence]


class ReviewCommentaryEvent(ReviewCommentarySummary):
    strokeIndex: int = Field(ge=0)
    frame: int = Field(ge=0)
    timeSec: float = Field(ge=0)
    player: Literal["a", "b"]


class ReviewCommentary(BaseModel):
    status: Literal["available", "unavailable", "unsupported"]
    source: Literal["full-match", "on-demand"] | None
    summary: ReviewCommentarySummary | None
    events: list[ReviewCommentaryEvent]
    unsupportedReason: str | None = Field(default=None, exclude_if=lambda value: value is None)


class ReviewCourtPosition(BaseModel):
    x: float
    y: float
    coordinateSpace: Literal["court_normalized_v1"]
    source: Literal["ankle_midpoint", "nearby_frame_ankle_midpoint", "single_ankle", "bbox_bottom_center"]
    confidence: float | None = Field(default=None, ge=0, le=1, exclude_if=lambda value: value is None)
    sourceFrame: int = Field(ge=0)


class ReviewStroke(BaseModel):
    eventIndex: int = Field(ge=0)
    strokeIndex: int = Field(ge=0)
    frame: int = Field(ge=0)
    time: float = Field(ge=0)
    ordinal: int = Field(gt=0)
    player: str
    type: str | None
    confidence: float | None
    hitter: Literal["a", "b"] | None = None
    hitterSide: Literal["top", "bottom"] | None = None
    courtPosition: ReviewCourtPosition | None = Field(default=None, exclude_if=lambda value: value is None)
    positionQuality: Literal["measured", "estimated", "unresolved"] | None = Field(default=None, exclude_if=lambda value: value is None)
    positionSource: Literal["ankle_midpoint", "nearby_frame_ankle_midpoint", "single_ankle", "bbox_bottom_center"] | None = Field(default=None, exclude_if=lambda value: value is None)
    positionUnavailableReason: Literal["HITTER_UNRESOLVED", "POSE_UNAVAILABLE", "ANKLES_UNAVAILABLE",
        "COURT_TRANSFORM_UNAVAILABLE", "OUT_OF_COURT"] | None = Field(default=None, exclude_if=lambda value: value is None)


class ReviewAudio(BaseModel):
    segmentIndex: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    intensity: float | None = Field(default=None, ge=0, le=1)
    windowCount: int = Field(ge=0)


class ReviewRally(BaseModel):
    id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    duration: float = Field(ge=0)
    score: tuple[int, int] | None
    game: int | None = Field(default=None, ge=0)
    gameSource: Literal["scores", "identity"] | None = Field(default=None, exclude_if=lambda value: value is None)
    gameConflict: str | None = Field(default=None, exclude_if=lambda value: value is None)
    identity: dict[Literal["top", "bottom"], Literal["a", "b"]] | None = Field(default=None, exclude_if=lambda value: value is None)
    scoreIssue: str | None = Field(default=None, exclude_if=lambda value: value is None)
    multi: bool
    subScores: list[tuple[int, int]]
    splits: list[float]
    hits: list[ReviewStroke] | None
    audio: ReviewAudio | None
    highlight: float | None = Field(default=None, ge=0, le=1)
    commentary: ReviewCommentary


class ReviewCheerWindow(BaseModel):
    segmentIndex: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    time: float = Field(ge=0)
    score: float = Field(ge=0, le=1)


class ReviewCommentaryAvailability(BaseModel):
    coverage: Literal["none", "partial", "complete"]
    availableRallyCount: int = Field(ge=0)
    unsupportedRallyCount: int = Field(ge=0)
    totalRallyCount: int = Field(ge=0)


class ReviewOverlay(BaseModel):
    """Where the per-rally video overlay files live and how to draw them.

    Pose and shuttle are far too large for the Review model, so each rally's drawable
    geometry is a separate file under ``url``; only ``segments`` have one.
    """

    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal["review-overlay-v1"]
    url: str = Field(min_length=1)
    segments: list[int]
    methods: list[str]
    baseMethod: str | None
    courtLines: list[tuple[int, int]]
    skeleton: list[tuple[int, int]]


class ReviewExport(BaseModel):
    """Stable envelope consumed by Vue; presentation state is absent."""

    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal[REVIEW_EXPORT_SCHEMA_VERSION]
    title: str
    video: str
    duration: float = Field(gt=0)
    scenario: str
    players: dict[Literal["a", "b"], str]
    capabilities: dict[str, bool]
    states: dict[str, ReviewStageState]
    rallies: list[ReviewRally]
    cheerTimeline: list[ReviewCheerWindow] | None = Field(default=None, exclude_if=lambda value: value is None)
    commentaryAvailability: ReviewCommentaryAvailability
    fps: float = Field(gt=0)
    source: ReviewSource
    overlay: ReviewOverlay | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def base_shape(self) -> "ReviewExport":
        if set(self.players) != {"a", "b"}:
            raise ValueError("players must define scoreboard rows a and b")
        return self


ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
    "match_segmentation": SegmentsArtifact,
    "score_recognition": ScoresArtifact,
    "event_detection": EventsArtifact,
    "stroke_classification": StrokesArtifact,
    "audio_highlight": AudioArtifact,
    "highlight_ranking": HighlightsArtifact,
    "player_identity": IdentityArtifact,
    "court_detection": CourtArtifact,
    "pose": PoseArtifact,
    "shuttle_tracking": ShuttleArtifact,
}
