"""Untrusted v2 generator and batch reviewer wire contracts."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from modules.commentary.facts.observations import ObservationModel, ObservationText
from modules.commentary.schemas import FactId


class ClaimReference(ObservationModel):
    fact_id: FactId
    # Unsupported capabilities are candidate rejections, not parser failures.
    field: Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]


class ObservationProposal(ObservationModel):
    observation: ObservationText
    supporting_claims: Annotated[list[ClaimReference], Field(min_length=2, max_length=12)]


class ObservationResponse(ObservationModel):
    observations: Annotated[list[ObservationProposal], Field(max_length=5)]


ViolationCode = Literal[
    "text_evidence_mismatch", "unsupported_identity", "unsupported_outcome",
    "unsupported_intent_or_causality", "unsupported_motion_or_physics",
    "unsupported_stroke_side", "language_or_format",
]


class CandidateVerdict(ObservationModel):
    candidate_id: FactId
    verdict: Literal["pass", "reject", "uncertain"]
    violation_codes: Annotated[list[ViolationCode], Field(max_length=7)]

    @model_validator(mode="after")
    def consistent_verdict(self):
        if len(set(self.violation_codes)) != len(self.violation_codes):
            raise ValueError("duplicate violation codes")
        if self.verdict == "pass" and self.violation_codes:
            raise ValueError("pass cannot contain violations")
        if self.verdict == "reject" and not self.violation_codes:
            raise ValueError("reject requires a violation reason")
        return self


class ReviewResponse(ObservationModel):
    verdicts: Annotated[list[CandidateVerdict], Field(min_length=1, max_length=5)]
