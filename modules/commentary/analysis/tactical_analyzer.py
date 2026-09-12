"""One rally request followed by deterministic provenance checks.

Gemini selects observation candidates. It cannot supply prose that is silently
promoted to a verified fact. Checks establish source grounding, not tactical
semantic truth. No stage registration, persistence, or commentator lives here.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from modules.common.bst.classes import CLASSES_8
from modules.commentary.facts.schemas import CompactRallyFacts, CompactStrokeFact
from modules.commentary.facts.tactical import TacticalAnalysisResult, TacticalFact
from modules.commentary.providers.base import LLMProvider, TokenUsage, require_text
from modules.commentary.schemas import RallyAnalysis, RallyFact, RallyFactEvent
from .rally_analyzer import RELIABLE_CONFIDENCE, analyze_rally
from .tactical_response import TacticalProposal, TacticalResponse

TACTICAL_PROMPT_VERSION = "tactical-analyzer-v1"
PROMPT_PATH = Path(__file__).parents[1] / "prompts" / "tactical_analyzer.txt"

_ANALYSIS_PATTERN = {
    "sustained_attack": "sustained_attack",
    "attack_transition": "lift_to_attack_transition",
    "rear_to_front_stroke_transition": "rear_court_stroke_to_front_court_stroke",
}
_DESCRIPTIONS = {
    "notable_stroke_sequence": "候選球種序列觀察，僅描述所引擊球事實。",
    "sustained_attack": "同一球員在所引擊球中兩度使用殺球或撲球。",
    "attack_transition": "所引後場球種之後銜接殺球或撲球。",
    "rear_to_front_stroke_transition": "所引球種由後場球種轉為網前球種，不表示球員移動路徑。",
    "front_back_court_displacement": "同一球員在所引擊球觀察點的前後場位置不同，不表示連續跑動。",
}


class TacticalAnalysisError(ValueError):
    """Invalid input/structured response; provider errors remain ProviderError."""


class TacticalAnalyzerResult(TacticalAnalysisResult):
    # SDK-neutral metadata is attached here, not to generic domain schemas.
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class _Evidence:
    event: CompactStrokeFact
    position: int
    kind: str
    eligible: bool


def _validated_inputs(compact: CompactRallyFacts, analysis: RallyAnalysis | None):
    try:
        compact = CompactRallyFacts.model_validate(compact.model_dump())
        fact = RallyFact(
            segment_index=compact.segment_index, game_index=None,
            start_sec=compact.start_sec, end_sec=compact.end_sec,
            duration_sec=compact.end_sec - compact.start_sec,
            score=compact.score, server=compact.server, highlight_score=None,
            rally_length=len(compact.events),
            events=[RallyFactEvent(**event.model_dump(include={
                "event_index", "frame", "time_sec", "player", "stroke_type", "stroke_confidence",
            })) for event in compact.events],
        )
        tolerance = .001 + 1e-9
        if compact.end_frame < compact.start_frame:
            raise ValueError("invalid compact frame range")
        if any(abs(frame / compact.fps - seconds) > tolerance for frame, seconds in (
            (compact.start_frame, compact.start_sec), (compact.end_frame, compact.end_sec),
        )):
            raise ValueError("compact frame/time disagreement")
        for event in compact.events:
            if (not compact.start_frame <= event.frame <= compact.end_frame
                    or abs(event.time_sec - event.frame / compact.fps) > 1e-9):
                raise ValueError("compact event frame/time disagreement")
        expected = analyze_rally(fact)
        if analysis is not None and analysis.model_dump() != expected.model_dump():
            raise ValueError("deterministic analysis does not match selected compact rally")
        return compact, expected
    except (ValueError, TypeError) as exc:
        raise TacticalAnalysisError(f"Invalid tactical input: {exc}") from exc


def _evidence_catalog(compact: CompactRallyFacts) -> dict[str, _Evidence]:
    catalog = {}
    for position, event in enumerate(compact.events):
        prefix = f"rally:{compact.segment_index}:stroke:{event.event_index}"
        reliable = (event.player is not None and event.stroke_type in CLASSES_8
                    and event.stroke_confidence is not None
                    and event.stroke_confidence >= RELIABLE_CONFIDENCE)
        rows = [(event.fact_id, prefix, "stroke", reliable)]
        for kind, optional in (("pose", event.pose), ("court", event.court_position),
                               ("shuttle", event.shuttle_path)):
            if optional is not None:
                eligible = reliable and kind == "court" and optional.quality == "reliable"
                rows.append((optional.fact_id, f"{prefix}:{kind}", kind, eligible))
        for fact_id, expected_id, kind, eligible in rows:
            if fact_id != expected_id or fact_id in catalog:
                raise TacticalAnalysisError("Invalid or duplicate source fact ID for selected rally")
            catalog[fact_id] = _Evidence(event, position, kind, eligible)
    return catalog


def _rejection(candidate: TacticalProposal, catalog: dict[str, _Evidence],
               analysis: RallyAnalysis) -> str | None:
    if any(fact_id not in catalog for fact_id in candidate.evidence_fact_ids):
        return "unknown_evidence_id"
    evidence = [catalog[fact_id] for fact_id in candidate.evidence_fact_ids]
    if any(not item.eligible for item in evidence):
        return "weak_unknown_or_context_only_evidence"
    indices = [item.event.event_index for item in evidence]
    positions = [item.position for item in evidence]
    if len(set(indices)) < 2:
        return "insufficient_evidence_events"
    if positions != sorted(positions):
        return "evidence_order_mismatch"
    # Endpoints are source identities of the first/last cited observations,
    # not numeric minima/maxima. Equality also establishes endpoint membership.
    if candidate.start_event_index != indices[0] or candidate.end_event_index != indices[-1]:
        return "event_range_mismatch"
    players = sorted({item.event.player for item in evidence})
    if sorted(candidate.players) != players:
        return "player_association_mismatch"
    if candidate.pattern_type == "front_back_court_displacement":
        if any(item.kind != "court" for item in evidence) or len(players) != 1:
            return "court_support_required"
        zones = {item.event.court_position.depth_zone for item in evidence}
        if len(zones) < 2:
            return "distinct_observed_depths_required"
    else:
        if any(item.kind != "stroke" for item in evidence):
            return "stroke_support_required"
        pattern_name = _ANALYSIS_PATTERN.get(candidate.pattern_type)
        if pattern_name is not None and not any(
            pattern.name == pattern_name
            and pattern.supporting_fact_ids == candidate.evidence_fact_ids
            for pattern in analysis.patterns
        ):
            return "deterministic_pattern_support_mismatch"
    return None


def _parse_response(text: str) -> TacticalResponse:
    def reject_constant(value):
        raise ValueError(f"non-JSON constant: {value}")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        # Strict complete JSON, no fence/substring repair or silent key overwrite.
        data = json.loads(require_text(text), parse_constant=reject_constant,
                          object_pairs_hook=unique_keys)
        return TacticalResponse.model_validate(data)
    except (ValueError, ValidationError) as exc:
        raise TacticalAnalysisError("Invalid structured tactical response") from exc


def analyze_tactical_facts(*, provider: LLMProvider, compact_facts: CompactRallyFacts,
                          deterministic_analysis: RallyAnalysis | None = None,
                          max_facts: int = 5) -> TacticalAnalyzerResult:
    """One request per rally; [] is valid, component failures remain explicit.

    Supplied analysis is verified against the unchanged deterministic analyzer.
    If omitted, it is computed locally from compact source observations.
    """
    if type(max_facts) is not int or not 1 <= max_facts <= 5:
        raise ValueError("max_facts must be an integer from 1 to 5")
    compact, analysis = _validated_inputs(compact_facts, deterministic_analysis)
    catalog = _evidence_catalog(compact)
    payload = {
        "prompt_version": TACTICAL_PROMPT_VERSION, "max_facts": max_facts,
        # Scores are unnecessary for v1 tactical selection; highlight is omitted.
        "compact_rally_facts": compact.model_dump(mode="json", exclude={"score", "server"}),
        "deterministic_analysis": analysis.model_dump(mode="json"),
        "evidence_catalog": [dict(fact_id=fact_id, kind=item.kind,
            event_index=item.event.event_index, position=item.position,
            player=item.event.player, eligible=item.eligible)
            for fact_id, item in catalog.items()],
    }
    response = provider.generate(
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        user_prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        response_schema=TacticalResponse,
    )
    generated = _parse_response(response.text)
    if generated.segment_index != compact.segment_index:
        raise TacticalAnalysisError("Tactical response segment_index does not match selected rally")
    if len(generated.facts) > max_facts:
        raise TacticalAnalysisError("Tactical response exceeds max_facts")
    facts, warnings, seen = [], [], set()
    for source_index, proposal in enumerate(generated.facts):
        rejection = _rejection(proposal, catalog, analysis)
        signature = (proposal.pattern_type, proposal.start_event_index, proposal.end_event_index,
                     tuple(sorted(proposal.players)), tuple(proposal.evidence_fact_ids))
        if rejection is None and signature in seen:
            rejection = "duplicate_candidate"
        if rejection is not None:
            warnings.append(f"rejected_tactical_fact:{source_index}:{rejection}")
            continue
        seen.add(signature)
        digest = hashlib.sha256(json.dumps(signature, separators=(",", ":")).encode()).hexdigest()[:16]
        confidence = min(catalog[ref].event.stroke_confidence for ref in proposal.evidence_fact_ids)
        salience = next((p.salience for p in analysis.patterns
                        if p.name == _ANALYSIS_PATTERN.get(proposal.pattern_type)
                        and p.supporting_fact_ids == proposal.evidence_fact_ids), .5)
        facts.append(TacticalFact(
            fact_id=f"rally:{compact.segment_index}:tactical:{digest}",
            segment_index=compact.segment_index,
            pattern_type=proposal.pattern_type,
            description=_DESCRIPTIONS[proposal.pattern_type],
            confidence=confidence, salience=salience,
            start_event_index=proposal.start_event_index, end_event_index=proposal.end_event_index,
            players=sorted(proposal.players), evidence_fact_ids=proposal.evidence_fact_ids,
            limitations=["grounding_checked_interpretation_not_proven",
                         "confidence_is_minimum_source_classifier_score_not_tactical_probability",
                         "salience_is_heuristic_priority_not_probability",
                         "observations_do_not_establish_intent_causality_or_continuous_movement"],
        ))
    positions = {event.event_index: position for position, event in enumerate(compact.events)}
    facts.sort(key=lambda fact: (positions[fact.start_event_index],
                                 positions[fact.end_event_index], fact.fact_id))
    if not facts:
        warnings.append("no_supported_tactical_observations")
    return TacticalAnalyzerResult(
        schema_version="tactical-facts-v1", prompt_version=TACTICAL_PROMPT_VERSION,
        provider_model=response.model, usage=response.usage,
        segment_index=compact.segment_index, facts=facts, warnings=warnings,
    )
