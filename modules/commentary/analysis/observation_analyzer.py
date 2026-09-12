"""One generation, zero/one batch semantic review; no repairs or retries."""

import json
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from modules.commentary.facts.observations import ObservationModel, TacticalObservation
from modules.commentary.facts.schemas import CompactRallyFacts
from modules.commentary.providers.base import LLMProvider, TokenUsage, require_text
from modules.commentary.schemas import FactId, NonNegativeFloat, NonNegativeInt, RallyAnalysis
from .observation_grounding import CAPABILITIES, ground_candidate, minimal_review_context
from .observation_response import ObservationResponse, ReviewResponse, CandidateVerdict
from .tactical_analyzer import TacticalAnalysisError, _validated_inputs, _evidence_catalog

GENERATOR_VERSION = "tactical-observation-generator-v2"
REVIEWER_VERSION = "tactical-observation-reviewer-v2"
PROMPT_DIR = Path(__file__).parents[1] / "prompts"


class ObservationAnalysisError(TacticalAnalysisError):
    def __init__(self, stage: str, message: str):
        super().__init__(f"{stage}: {message}")
        self.stage = stage


class CallMetadata(ObservationModel):
    prompt_version: FactId
    returned_model: str | None
    usage: TokenUsage | None
    latency_seconds: NonNegativeFloat


class GroundingRejection(ObservationModel):
    proposal_index: NonNegativeInt
    reason: FactId


class ObservationAnalysisResult(ObservationModel):
    schema_version: Literal["tactical-observations-v2"] = "tactical-observations-v2"
    segment_index: NonNegativeInt
    outcome: Literal["generated_empty", "grounding_rejected", "semantic_rejected", "accepted"]
    observations: Annotated[list[TacticalObservation], Field(max_length=5)]
    raw_proposal_count: Annotated[int, Field(ge=0, le=5)]
    grounding_rejections: Annotated[list[GroundingRejection], Field(max_length=5)]
    review_verdicts: Annotated[list[CandidateVerdict], Field(max_length=5)]
    generation: CallMetadata
    review: CallMetadata | None

    @model_validator(mode="after")
    def valid_result(self):
        ids = [o.fact_id for o in self.observations]
        if len(ids) != len(set(ids)) or any(o.segment_index != self.segment_index for o in self.observations):
            raise ValueError("observations must have unique IDs in selected rally")
        if bool(self.observations) != (self.outcome == "accepted"):
            raise ValueError("outcome must agree with accepted observations")
        reviewed_ids = [v.candidate_id for v in self.review_verdicts]
        if len(reviewed_ids) != len(set(reviewed_ids)):
            raise ValueError("review verdict IDs must be unique")
        if set(ids) != {v.candidate_id for v in self.review_verdicts if v.verdict == "pass"}:
            raise ValueError("accepted observations must match passed reviews")
        if self.raw_proposal_count != len(self.grounding_rejections) + len(self.review_verdicts):
            raise ValueError("proposal diagnostics must account for every candidate")
        if bool(self.review_verdicts) != (self.review is not None):
            raise ValueError("review metadata required exactly when review occurred")
        expected = ("accepted" if ids else "semantic_rejected" if self.review is not None
                    else "grounding_rejected" if self.raw_proposal_count else "generated_empty")
        if self.outcome != expected:
            raise ValueError("outcome must match phase diagnostics")
        return self


def _parse(text, schema, stage):
    def unique_keys(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate JSON key")
            data[key] = value
        return data

    def no_constant(_):
        raise ValueError("non-JSON constant")

    try:
        data = json.loads(require_text(text), object_pairs_hook=unique_keys, parse_constant=no_constant)
        return schema.model_validate(data)
    except (ValueError, ValidationError) as exc:
        # Do not echo model content or prompts in error messages.
        raise ObservationAnalysisError(stage, "invalid structured response") from exc


def _generate(provider, payload, schema, version):
    started = perf_counter()
    response = provider.generate(
        system_prompt=(PROMPT_DIR / f"{version}.txt").read_text(encoding="utf-8"),
        user_prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        response_schema=schema,
    )
    metadata = CallMetadata(prompt_version=version, returned_model=response.model,
        usage=response.usage, latency_seconds=perf_counter() - started)
    return _parse(response.text, schema, version), metadata


def analyze_tactical_observations(*, generator: LLMProvider, reviewer: LLMProvider,
        compact_facts: CompactRallyFacts,
        deterministic_analysis: RallyAnalysis | None = None) -> ObservationAnalysisResult:
    """Providers may be the same instance. Each role is called at most once.

    ProviderError (including safe diagnostics) propagates unchanged. Broken wire
    contracts raise ObservationAnalysisError; neither becomes a successful [].
    """
    compact, analysis = _validated_inputs(compact_facts, deterministic_analysis)
    catalog = _evidence_catalog(compact)
    payload = dict(
        compact_rally_facts=compact.model_dump(mode="json", exclude={"score", "server"}),
        optional_pattern_hints=[p.model_dump(mode="json") for p in analysis.patterns],
        source_catalog=[dict(fact_id=ref, position=item.position,
            allowed_fields=list(CAPABILITIES.get(item.kind, ())) if item.eligible else [])
            for ref, item in catalog.items()],
    )
    generated, generation = _generate(generator, payload, ObservationResponse, GENERATOR_VERSION)
    grounded, rejections, seen = [], [], set()
    for index, proposal in enumerate(generated.observations):
        candidate, reason = ground_candidate(proposal, catalog, compact.segment_index)
        if candidate is not None and candidate.candidate_id in seen:
            reason = "duplicate_candidate"
        if reason is not None:
            rejections.append(GroundingRejection(proposal_index=index, reason=reason))
        else:
            seen.add(candidate.candidate_id)
            grounded.append(candidate)
    grounded.sort(key=lambda c: (c.claims[0].position, c.claims[-1].position, c.candidate_id))
    common = dict(segment_index=compact.segment_index, raw_proposal_count=len(generated.observations),
                  grounding_rejections=rejections, generation=generation)
    if not grounded:
        return ObservationAnalysisResult(**common, observations=[], review=None, review_verdicts=[],
            outcome="generated_empty" if not generated.observations else "grounding_rejected")
    reviewed, review = _generate(reviewer,
        dict(candidates=[minimal_review_context(c, compact) for c in grounded]),
        ReviewResponse, REVIEWER_VERSION)
    verdict_ids = [v.candidate_id for v in reviewed.verdicts]
    if len(verdict_ids) != len(set(verdict_ids)) or set(verdict_ids) != seen:
        raise ObservationAnalysisError("review", "expected exactly one verdict per grounded candidate")
    verdicts = {v.candidate_id: v for v in reviewed.verdicts}
    accepted = []
    for candidate in grounded:
        if verdicts[candidate.candidate_id].verdict != "pass":
            continue
        claims = candidate.claims
        accepted.append(TacticalObservation(
            fact_id=candidate.candidate_id, segment_index=compact.segment_index,
            observation=candidate.observation, start_event_index=claims[0].event_index,
            end_event_index=claims[-1].event_index, players=sorted({c.player for c in claims}),
            evidence_fact_ids=list(dict.fromkeys(c.fact_id for c in claims)), supporting_claims=claims,
            limitations=["grounding_checked_interpretation_not_proven",
                "semantic_review_is_model_judgment_not_proof",
                "source_classifier_confidence_is_not_tactical_probability"],
            grounding_status="validated", semantic_review="passed", epistemic_status="model_interpretation",
        ))
    return ObservationAnalysisResult(**common, observations=accepted, review=review,
        review_verdicts=[verdicts[c.candidate_id] for c in grounded],
        outcome="accepted" if accepted else "semantic_rejected")
