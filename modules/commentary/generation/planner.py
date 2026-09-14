"""Deterministic Commentator input construction; no LLM planning call."""

from typing import Annotated, Literal

from pydantic import Field

from modules.commentary.analysis.observation_grounding import ground_candidate
from modules.commentary.analysis.observation_response import ClaimReference, ObservationProposal
from modules.commentary.analysis.rally_analyzer import analyze_rally
from modules.commentary.analysis.tactical_analyzer import _evidence_catalog
from modules.commentary.facts.observations import TacticalObservation
from modules.commentary.facts.schemas import CompactRallyFacts
from modules.commentary.identity import CourtPositionToPlayer
from modules.commentary.schemas import (
    FactId,
    NonNegativeFloat,
    NonNegativeInt,
    Player,
    Probability,
    RallyFact,
    StrictModel,
    StrokeConfidenceBand,
)


class CommentaryPlanningError(ValueError):
    """Trusted inputs do not describe the same selected rally."""


class CourtZoneContext(StrictModel):
    fact_id: FactId
    depth_zone: Literal["rear", "mid", "front"]
    width_zone: Literal["left", "center", "right"]
    limitations: list[str]


class CommentaryPlanEvent(StrictModel):
    position: NonNegativeInt
    event_index: NonNegativeInt
    source_fact_id: FactId
    frame: NonNegativeInt
    time_sec: NonNegativeFloat
    player: Player | None
    stroke_type: str | None
    source_classifier_confidence: Probability | None
    confidence_band: StrokeConfidenceBand
    eligible_for_output: bool
    court_zone: CourtZoneContext | None
    limitations: list[str]


class HighlightContext(StrictModel):
    ranking_score: Probability
    semantics: Literal["context_only_not_probability_or_event_evidence"] = (
        "context_only_not_probability_or_event_evidence"
    )


class CommentaryPlan(StrictModel):
    plan_version: Literal["commentary-plan-v1"] = "commentary-plan-v1"
    segment_index: NonNegativeInt
    identity_mapping: CourtPositionToPlayer
    events: list[CommentaryPlanEvent]
    rally_limitations: list[str]
    required_event_indices: list[NonNegativeInt]
    tactical_observations: list[TacticalObservation]
    highlight_context: HighlightContext | None
    summary_requested: bool
    score_context: Literal["withheld_no_outcome_inference"] = "withheld_no_outcome_inference"


def _validate_rally_alignment(fact: RallyFact, compact: CompactRallyFacts) -> None:
    if fact.segment_index != compact.segment_index:
        raise CommentaryPlanningError("RallyFact and CompactRallyFacts segment mismatch")
    if (fact.start_sec, fact.end_sec, fact.score, fact.server) != (
            compact.start_sec, compact.end_sec, compact.score, compact.server):
        raise CommentaryPlanningError("RallyFact and CompactRallyFacts context mismatch")
    if len(fact.events) != len(compact.events):
        raise CommentaryPlanningError("RallyFact and CompactRallyFacts event count mismatch")
    for position, (source, item) in enumerate(zip(fact.events, compact.events, strict=True)):
        expected = (source.event_index, source.frame, source.time_sec, source.player,
                    source.stroke_type, source.stroke_confidence)
        actual = (item.event_index, item.frame, item.time_sec, item.player,
                  item.stroke_type, item.stroke_confidence)
        if expected != actual or item.fact_id != f"rally:{fact.segment_index}:stroke:{source.event_index}":
            raise CommentaryPlanningError(f"canonical event mismatch at compact position {position}")


def _validated_observations(
    observations: list[TacticalObservation], compact: CompactRallyFacts,
) -> list[TacticalObservation]:
    catalog = _evidence_catalog(compact)
    checked = []
    seen = set()
    for raw in observations:
        try:
            # Revalidate existing model instances too; model_copy(update=...) can
            # otherwise bypass field/model validators before crossing this boundary.
            value = raw.model_dump(mode="python") if isinstance(raw, TacticalObservation) else raw
            observation = TacticalObservation.model_validate(value, strict=True)
            proposal = ObservationProposal(
                observation=observation.observation,
                supporting_claims=[ClaimReference(fact_id=c.fact_id, field=c.field)
                                   for c in observation.supporting_claims],
            )
            grounded, reason = ground_candidate(proposal, catalog, compact.segment_index)
        except Exception as exc:
            raise CommentaryPlanningError("invalid tactical observation input") from exc
        if reason is not None or grounded is None:
            raise CommentaryPlanningError(f"tactical observation failed grounding: {reason}")
        if (observation.fact_id != grounded.candidate_id
                or observation.supporting_claims != grounded.claims
                or observation.start_event_index != grounded.claims[0].event_index
                or observation.end_event_index != grounded.claims[-1].event_index
                or observation.players != sorted({c.player for c in grounded.claims})
                or observation.evidence_fact_ids != list(dict.fromkeys(c.fact_id for c in grounded.claims))):
            raise CommentaryPlanningError("tactical observation provenance does not match current rally")
        if observation.fact_id in seen:
            raise CommentaryPlanningError("duplicate tactical observation ID")
        seen.add(observation.fact_id)
        checked.append(observation)
    positions = {event.event_index: position for position, event in enumerate(compact.events)}
    checked.sort(key=lambda o: (
        positions[o.start_event_index], positions[o.end_event_index], o.fact_id,
    ))
    return checked


def build_commentary_plan(*, rally_fact: RallyFact, compact_facts: CompactRallyFacts,
        court_position_to_player: CourtPositionToPlayer,
        tactical_observations: list[TacticalObservation] | None = None,
        include_summary: bool = True) -> CommentaryPlan:
    """Package canonical context and output coverage without deriving new tactics."""
    try:
        rally_fact = RallyFact.model_validate(rally_fact.model_dump(mode="python"))
        compact_facts = CompactRallyFacts.model_validate(compact_facts.model_dump(mode="python"))
        court_position_to_player = CourtPositionToPlayer.model_validate(
            court_position_to_player.model_dump(mode="python"))
    except Exception as exc:
        raise CommentaryPlanningError("invalid trusted commentary input") from exc
    _validate_rally_alignment(rally_fact, compact_facts)
    observations = _validated_observations(tactical_observations or [], compact_facts)
    analyzed = {item.event_index: item for item in analyze_rally(rally_fact).candidate_strokes}
    events = []
    for position, event in enumerate(compact_facts.events):
        court = event.court_position
        court_context = None
        if court is not None and court.quality == "reliable":
            court_context = CourtZoneContext(
                fact_id=court.fact_id, depth_zone=court.depth_zone,
                width_zone=court.width_zone, limitations=court.limitations,
            )
        analysis = analyzed[event.event_index]
        limitations = list(dict.fromkeys([
            *event.warnings,
            *(court.limitations if court_context is not None else []),
        ]))
        events.append(CommentaryPlanEvent(
            position=position, event_index=event.event_index, source_fact_id=event.fact_id,
            frame=event.frame, time_sec=event.time_sec, player=event.player,
            stroke_type=event.stroke_type,
            source_classifier_confidence=event.stroke_confidence,
            confidence_band=analysis.confidence_band,
            eligible_for_output=event.player is not None,
            court_zone=court_context, limitations=limitations,
        ))
    required = [event.event_index for event in events if event.eligible_for_output]
    return CommentaryPlan(
        segment_index=rally_fact.segment_index,
        identity_mapping=court_position_to_player,
        events=events, rally_limitations=list(compact_facts.warnings),
        required_event_indices=required,
        tactical_observations=observations,
        highlight_context=(HighlightContext(ranking_score=rally_fact.highlight_score)
                           if rally_fact.highlight_score is not None else None),
        summary_requested=bool(include_summary and required),
    )
