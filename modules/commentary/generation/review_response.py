"""Strict wire response authored by the batch commentary semantic reviewer."""

import json
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from modules.commentary.schemas import NonNegativeInt, StrictModel


ReviewVerdict = Literal["pass", "reject", "uncertain"]
ReviewViolationCode = Literal[
    "text_evidence_mismatch",
    "unsupported_spatial_claim",
    "unsupported_movement_claim",
    "unsupported_identity",
    "unsupported_intent_or_opportunity",
    "unsupported_causality",
    "unsupported_outcome_or_score",
    "unsupported_stroke_side",
    "unsupported_motion_or_physics",
    "unsupported_fine_stroke_class",
    "broadcast_interpretation",
]

ADVISORY_REVIEW_CODES = frozenset({"broadcast_interpretation"})


class ReviewWireModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class _Verdict(ReviewWireModel):
    verdict: ReviewVerdict
    violation_codes: Annotated[list[ReviewViolationCode], Field(max_length=11)]

    @model_validator(mode="after")
    def verdict_matches_codes(self):
        if self.violation_codes != sorted(set(self.violation_codes)):
            raise ValueError("violation_codes must be canonical and unique")
        if (self.verdict == "pass") != (not self.violation_codes):
            raise ValueError("pass requires no violations; reject/uncertain requires violations")
        if (self.violation_codes
                and set(self.violation_codes) <= ADVISORY_REVIEW_CODES
                and self.verdict != "uncertain"):
            raise ValueError("advisory-only diagnostics require uncertain verdict")
        return self


class CommentaryEventVerdict(_Verdict):
    event_index: NonNegativeInt


class CommentarySummaryVerdict(_Verdict):
    pass


class CommentaryReviewResponse(ReviewWireModel):
    events: Annotated[list[CommentaryEventVerdict], Field(min_length=1)]
    summary: CommentarySummaryVerdict | None

    @model_validator(mode="after")
    def unique_events(self):
        indexes = [item.event_index for item in self.events]
        if len(indexes) != len(set(indexes)):
            raise ValueError("review event_index must be unique")
        return self


def parse_commentary_review_response(text: str) -> CommentaryReviewResponse:
    """Parse complete JSON locally; provider-side schema checks are insufficient."""

    def unique_keys(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate JSON key")
            data[key] = value
        return data

    def reject_constant(_):
        raise ValueError("non-JSON numeric constant")

    try:
        data = json.loads(text, object_pairs_hook=unique_keys, parse_constant=reject_constant)
        return CommentaryReviewResponse.model_validate(data)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("invalid structured commentary review response") from exc


def has_hard_review_violation(verdict: _Verdict | None) -> bool:
    """Broadcast interpretation is diagnostic; every other code is fail-closed."""
    return bool(verdict is not None and set(verdict.violation_codes) - ADVISORY_REVIEW_CODES)
