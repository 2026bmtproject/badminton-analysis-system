"""V1 model proposals contain selections, never trusted free-form claims.

The broader PR A GeneratedTacticalFact remains a domain candidate schema, but
is deliberately not the provider wire contract: keyword blacklists cannot
guarantee that arbitrary prose contains no unsupported claim.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from modules.commentary.schemas import FactId, NonNegativeInt, Player, StrictModel

V1Pattern = Literal[
    "notable_stroke_sequence", "sustained_attack", "attack_transition",
    "rear_to_front_stroke_transition", "front_back_court_displacement",
]


class TacticalProposal(StrictModel):
    pattern_type: V1Pattern
    # Source references; temporal range validation needs the selected rally.
    start_event_index: NonNegativeInt
    end_event_index: NonNegativeInt
    players: Annotated[list[Player], Field(min_length=1, max_length=2)]
    evidence_fact_ids: Annotated[list[FactId], Field(min_length=2, max_length=12)]

    @model_validator(mode="after")
    def validate_structure(self):
        if len(set(self.players)) != len(self.players):
            raise ValueError("duplicate players")
        if len(set(self.evidence_fact_ids)) != len(self.evidence_fact_ids):
            raise ValueError("duplicate evidence IDs")
        return self


class TacticalResponse(StrictModel):
    segment_index: NonNegativeInt
    facts: Annotated[list[TacticalProposal], Field(max_length=5)]
