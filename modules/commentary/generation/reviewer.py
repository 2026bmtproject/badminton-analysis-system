"""One batch semantic evidence review for generated Commentator prose."""

import json
from pathlib import Path
from time import perf_counter
from typing import Literal

from modules.commentary.providers.base import LLMProvider, TokenUsage
from modules.commentary.schemas import NonNegativeFloat, StrictModel

from .planner import CommentaryPlan, CommentaryPlanEvent
from .response import CommentatorResponse
from .review_response import (
    CommentaryReviewResponse,
    parse_commentary_review_response,
)


COMMENTARY_REVIEWER_VERSION = "commentary-semantic-reviewer-v1"
REVIEW_PROMPT_PATH = (
    Path(__file__).parents[1] / "prompts" / f"{COMMENTARY_REVIEWER_VERSION}.txt"
)


class CommentarySemanticReviewError(ValueError):
    """Reviewer output cannot be joined safely to the generated event set."""

    def __init__(self, message, *, response=None, metadata=None):
        super().__init__(message)
        self.response = response
        self.metadata = metadata


class CommentaryReviewMetadata(StrictModel):
    prompt_version: Literal["commentary-semantic-reviewer-v1"] = COMMENTARY_REVIEWER_VERSION
    requested_model: str | None
    returned_model: str | None
    usage: TokenUsage | None
    latency_seconds: NonNegativeFloat
    provider_calls: Literal[0, 1]


def _event_context(event: CommentaryPlanEvent) -> dict:
    return {
        "position": event.position,
        "event_index": event.event_index,
        "player": event.player,
        "stroke_type": event.stroke_type,
        "source_classifier_confidence": event.source_classifier_confidence,
        "source_reliability": event.confidence_band,
        "source_limitations": event.limitations,
        "trusted_court_fact": (
            event.court_zone.model_dump(mode="json") if event.court_zone else None
        ),
    }


def _observation_context(observation) -> dict:
    return {
        "fact_id": observation.fact_id,
        "observation": observation.observation,
        "start_event_index": observation.start_event_index,
        "end_event_index": observation.end_event_index,
        "players": observation.players,
        "evidence_fact_ids": observation.evidence_fact_ids,
        "limitations": observation.limitations,
        "epistemic_status": observation.epistemic_status,
    }


def build_commentary_review_payload(
    plan: CommentaryPlan, generated: CommentatorResponse,
) -> dict:
    """Select only evidence needed to judge generated event and summary prose."""
    by_index = {item.event_index: item for item in generated.events}
    positions = {item.event_index: item.position for item in plan.events}
    observation_ranges = []
    for observation in plan.tactical_observations:
        observation_ranges.append((
            positions[observation.start_event_index],
            positions[observation.end_event_index],
            observation,
        ))
    events = []
    for event in plan.events:
        if not event.eligible_for_output:
            continue
        start = max(0, event.position - 1)
        stop = min(len(plan.events), event.position + 2)
        overlapping = [
            _observation_context(observation)
            for first, last, observation in observation_ranges
            if first <= event.position <= last
        ]
        events.append({
            "event_index": event.event_index,
            "generated_text": by_index[event.event_index].text,
            "trusted_court_facts_available": event.court_zone is not None,
            "canonical_event": _event_context(event),
            "immediate_chronology": [
                _event_context(item) for item in plan.events[start:stop]
            ],
            "overlapping_tactical_observations": overlapping,
        })
    trusted_courts = [
        {
            "event_index": event.event_index,
            "player": event.player,
            "court": event.court_zone.model_dump(mode="json"),
        }
        for event in plan.events if event.court_zone is not None
    ]
    summary = None
    if generated.summary is not None:
        summary = {
            "generated_text": generated.summary,
            "trusted_court_facts_available": bool(trusted_courts),
            "canonical_stroke_sequence": [_event_context(item) for item in plan.events],
            "trusted_court_facts": trusted_courts,
            "accepted_tactical_observations": [
                _observation_context(item) for item in plan.tactical_observations
            ],
            "relevant_limitations": list(dict.fromkeys(
                ["score_and_outcome_withheld", "highlight_is_context_only"]
                + plan.rally_limitations
                + [warning for event in plan.events for warning in event.limitations]
            )),
        }
    return {"events": events, "summary": summary}


def review_commentary(
    *, provider: LLMProvider, plan: CommentaryPlan, generated: CommentatorResponse,
    requested_model: str | None,
) -> tuple[CommentaryReviewResponse, CommentaryReviewMetadata]:
    payload = build_commentary_review_payload(plan, generated)
    started = perf_counter()
    response = provider.generate(
        system_prompt=REVIEW_PROMPT_PATH.read_text(encoding="utf-8"),
        user_prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        response_schema=CommentaryReviewResponse,
    )
    metadata = CommentaryReviewMetadata(
        requested_model=requested_model,
        returned_model=response.model,
        usage=response.usage,
        latency_seconds=perf_counter() - started,
        provider_calls=1,
    )
    try:
        reviewed = parse_commentary_review_response(response.text)
    except ValueError as exc:
        raise CommentarySemanticReviewError(
            "invalid structured commentary semantic review", metadata=metadata,
        ) from exc
    expected = set(plan.required_event_indices)
    actual = {item.event_index for item in reviewed.events}
    if actual != expected or len(reviewed.events) != len(expected):
        raise CommentarySemanticReviewError(
            "review must exactly cover eligible event indices",
            response=reviewed, metadata=metadata,
        )
    if (generated.summary is not None) != (reviewed.summary is not None):
        raise CommentarySemanticReviewError(
            "review summary presence must match generated summary",
            response=reviewed, metadata=metadata,
        )
    verdicts = {item.event_index: item for item in reviewed.events}
    ordered = reviewed.model_copy(update={
        "events": [verdicts[index] for index in plan.required_event_indices]
    })
    return ordered, metadata
