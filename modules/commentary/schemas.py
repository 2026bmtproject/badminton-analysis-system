"""Internal domain and text-response schemas adapted from badminton-commentary.

Upstream inputs remain modules.contracts types. event_index and stroke_index
always refer to the full-match event array. Generated text schemas exclude
frame/time; Python attaches verified source timing at the artifact boundary.
"""


from typing import Annotated, Literal


from pydantic import BaseModel, ConfigDict, Field


Player = Literal["a", "b"]
FactId = Annotated[str, Field(min_length=1, pattern=r"\S")]


NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]


NonNegativeFloat = Annotated[float, Field(ge=0)]


Probability = Annotated[float, Field(ge=0, le=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RallyScore(StrictModel):
    a: NonNegativeInt | None
    b: NonNegativeInt | None


class RallyFactEvent(StrictModel):
    event_index: NonNegativeInt
    frame: NonNegativeInt
    time_sec: NonNegativeFloat
    player: Player | None
    stroke_type: str | None
    stroke_confidence: Probability | None


class RallyFact(StrictModel):
    segment_index: NonNegativeInt
    game_index: NonNegativeInt | None
    start_sec: NonNegativeFloat
    end_sec: NonNegativeFloat
    duration_sec: NonNegativeFloat
    score: RallyScore
    server: Player | None
    events: list[RallyFactEvent]
    rally_length: NonNegativeInt
    highlight_score: Probability | None


StrokeConfidenceBand = Literal["reliable", "cautious", "low"]


StrokePatternName = Literal[
    "serve_return_pattern",
    "lift_to_attack_transition",
    "sustained_attack",
    "rear_court_stroke_to_front_court_stroke",
    "stroke_diversity",
]


class AnalyzedStroke(StrictModel):
    fact_id: FactId
    event_index: NonNegativeInt
    player: Player
    stroke_type: str
    confidence: Probability
    confidence_band: StrokeConfidenceBand
    salience: Probability


class StrokePattern(StrictModel):
    fact_id: FactId
    name: StrokePatternName
    salience: Probability
    commentary_hint: str
    supporting_fact_ids: Annotated[list[FactId], Field(min_length=2)]
    representative_fact_id: FactId | None


class RallyAnalysis(StrictModel):
    segment_index: NonNegativeInt
    reliable_stroke_count: NonNegativeInt
    cautious_stroke_count: NonNegativeInt
    excluded_stroke_count: NonNegativeInt
    opening_observed_stroke: AnalyzedStroke | None
    final_observed_stroke: AnalyzedStroke | None
    candidate_strokes: list[AnalyzedStroke]
    notable_strokes: list[AnalyzedStroke]
    patterns: list[StrokePattern]
    warnings: list[str]


class GeneratedCommentary(StrictModel):
    segment_index: NonNegativeInt
    text: Annotated[str, Field(min_length=1, max_length=240)]
    source_fact_ids: Annotated[list[FactId], Field(min_length=1)]


StrokeLocalFactName = Literal[
    "rear_exchange_continuation",
    "rear_court_stroke_to_front_court_stroke",
    "net_exchange_continuation",
    "flat_exchange_continuation",
    "net_to_lift_transition",
    "lift_to_attack_transition",
    "drop_lift_attack_sequence",
]


class StrokeLocalFact(StrictModel):
    fact_id: FactId
    name: StrokeLocalFactName
    start_stroke_index: NonNegativeInt
    end_stroke_index: NonNegativeInt
    salience: Probability
    commentary_hint: str
    supporting_fact_ids: Annotated[list[FactId], Field(min_length=2, max_length=3)]


class StrokeEventAnalysis(StrictModel):
    segment_index: NonNegativeInt
    stroke_index: NonNegativeInt
    frame: NonNegativeInt
    time_sec: NonNegativeFloat
    current_stroke: AnalyzedStroke
    previous_strokes: Annotated[list[AnalyzedStroke], Field(max_length=4)]
    local_facts: list[StrokeLocalFact]
    speaking_score: Probability
    should_speak: bool


class GeneratedStrokeText(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=120)]
    source_fact_ids: Annotated[list[FactId], Field(min_length=1)]


class GeneratedStrokeBatchItem(StrictModel):
    stroke_index: NonNegativeInt
    text: Annotated[str, Field(min_length=1, max_length=120)]
    source_fact_ids: Annotated[list[FactId], Field(min_length=1)]


class GeneratedRallyTextBatch(StrictModel):
    segment_index: NonNegativeInt
    events: list[GeneratedStrokeBatchItem]
    summary: GeneratedCommentary | None
