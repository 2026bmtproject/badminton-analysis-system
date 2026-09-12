"""V2 model interpretations; source grounding is not tactical truth."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from modules.commentary.schemas import FactId, NonNegativeInt, Player, Probability, StrictModel


class ObservationModel(StrictModel):
    model_config = ConfigDict(strict=True)


ObservationText = Annotated[str, Field(min_length=1, max_length=240, pattern=r"\S")]
# Observable capabilities, never a vocabulary of tactical interpretations.
SourceField = Literal["stroke_type", "depth_zone", "width_zone"]


class SupportingClaim(ObservationModel):
    fact_id: FactId
    field: SourceField
    value: Annotated[str, Field(min_length=1, max_length=80)]
    event_index: NonNegativeInt
    position: NonNegativeInt
    source_frame: NonNegativeInt
    player: Player
    source_classifier_confidence: Probability
    source_quality: Literal["reliable"]


class TacticalObservation(ObservationModel):
    fact_id: FactId
    segment_index: NonNegativeInt
    observation: ObservationText
    start_event_index: NonNegativeInt
    end_event_index: NonNegativeInt
    players: Annotated[list[Player], Field(min_length=1, max_length=2)]
    evidence_fact_ids: Annotated[list[FactId], Field(min_length=2, max_length=12)]
    supporting_claims: Annotated[list[SupportingClaim], Field(min_length=2, max_length=12)]
    limitations: Annotated[list[FactId], Field(min_length=1, max_length=8)]
    grounding_status: Literal["validated"]
    semantic_review: Literal["passed"]
    epistemic_status: Literal["model_interpretation"]

    @model_validator(mode="after")
    def canonical_provenance(self):
        claims = self.supporting_claims
        keys = [(c.position, c.fact_id, c.field) for c in claims]
        if keys != sorted(set(keys)):
            raise ValueError("supporting claims must be canonical and unique")
        if len({c.event_index for c in claims}) < 2:
            raise ValueError("at least two distinct source events required")
        if (self.start_event_index, self.end_event_index) != (claims[0].event_index, claims[-1].event_index):
            raise ValueError("event endpoints must follow canonical claim positions")
        if self.players != sorted({c.player for c in claims}):
            raise ValueError("players must match canonical claims")
        if self.evidence_fact_ids != list(dict.fromkeys(c.fact_id for c in claims)):
            raise ValueError("evidence IDs must match canonical claims")
        if any(c.fact_id != f"rally:{self.segment_index}:stroke:{c.event_index}"
               + ("" if c.field == "stroke_type" else ":court") for c in claims):
            raise ValueError("claim must belong to selected rally/event")
        if "grounding_checked_interpretation_not_proven" not in self.limitations:
            raise ValueError("interpretation limitation required")
        return self
