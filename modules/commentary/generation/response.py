"""Strict untrusted wire response authored by the Commentator."""

import json
from typing import Annotated

from pydantic import ConfigDict, Field, ValidationError, model_validator

from modules.commentary.schemas import NonNegativeInt, StrictModel


CommentaryEventText = Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]
CommentarySummaryText = Annotated[str, Field(min_length=1, max_length=240, pattern=r"\S")]


class CommentatorWireModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class GeneratedEventCommentary(CommentatorWireModel):
    event_index: NonNegativeInt
    text: CommentaryEventText


class CommentatorResponse(CommentatorWireModel):
    events: Annotated[list[GeneratedEventCommentary], Field(min_length=1)]
    summary: CommentarySummaryText | None

    @model_validator(mode="after")
    def unique_events(self):
        indexes = [event.event_index for event in self.events]
        if len(indexes) != len(set(indexes)):
            raise ValueError("generated event_index must be unique")
        return self


def parse_commentator_response(text: str) -> CommentatorResponse:
    """Parse complete JSON locally; provider-side schema enforcement is insufficient."""

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
        return CommentatorResponse.model_validate(data)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("invalid structured Commentator response") from exc
