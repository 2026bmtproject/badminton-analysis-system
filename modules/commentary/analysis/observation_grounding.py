"""Resolve source capabilities and select bounded-field temporal context.

PR #7 input/catalog validation is shared without changing v1 behavior. Pattern
helpers are never used for v2 acceptance or chronology.
"""

import hashlib
import json
from dataclasses import dataclass

from modules.commentary.facts.observations import SupportingClaim
from modules.commentary.facts.schemas import CompactRallyFacts
from .observation_response import ObservationProposal
from .tactical_analyzer import _Evidence


CAPABILITIES = {"stroke": ("stroke_type",), "court": ("depth_zone", "width_zone")}


@dataclass(frozen=True)
class GroundedCandidate:
    candidate_id: str
    observation: str
    claims: list[SupportingClaim]


def ground_candidate(proposal: ObservationProposal, catalog: dict[str, _Evidence],
                     segment_index: int) -> tuple[GroundedCandidate | None, str | None]:
    claims = {}
    for ref in proposal.supporting_claims:
        source = catalog.get(ref.fact_id)
        if source is None:
            return None, "unknown_evidence_id"
        if ref.field not in CAPABILITIES.get(source.kind, ()):
            return None, "unsupported_source_field"
        if not source.eligible:
            return None, "weak_unknown_or_context_only_evidence"
        event = source.event
        owner = event if source.kind == "stroke" else event.court_position
        value = getattr(owner, ref.field, None)
        if value is None:
            return None, "missing_source_value"
        claim = SupportingClaim(
            fact_id=ref.fact_id, field=ref.field, value=value,
            event_index=event.event_index, position=source.position, player=event.player,
            source_frame=event.frame if source.kind == "stroke" else owner.source_frame,
            source_classifier_confidence=event.stroke_confidence, source_quality="reliable",
        )
        claims[(source.position, ref.fact_id, ref.field)] = claim
    canonical = [claims[key] for key in sorted(claims)]
    if len({c.position for c in canonical}) < 2:
        return None, "insufficient_evidence_events"
    text = proposal.observation.strip()
    signature = {"version": "tactical-observations-v2", "segment_index": segment_index,
                 "observation": text, "claims": [c.model_dump(mode="json") for c in canonical]}
    digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return GroundedCandidate(f"rally:{segment_index}:observation:{digest}", text, canonical), None


def minimal_review_context(candidate: GroundedCandidate, compact: CompactRallyFacts) -> dict:
    """Complete cited span plus one event before/after; only basic source fields.

    Keeping every intervening hit (including unknown/weak hits) exposes false
    consecutive claims. An observation spanning the rally needs the whole span,
    but never gets unrelated pose/shuttle, scores or full compact payloads.
    """
    start, end = candidate.claims[0].position, candidate.claims[-1].position
    lo, hi = max(0, start - 1), min(len(compact.events), end + 2)
    context = []
    for position in range(lo, hi):
        event = compact.events[position]
        context.append(dict(position=position, event_index=event.event_index,
            player=event.player, stroke_type=event.stroke_type,
            source_classifier_confidence=event.stroke_confidence))
    return dict(candidate_id=candidate.candidate_id, observation=candidate.observation,
        supporting_claims=[c.model_dump(mode="json") for c in candidate.claims],
        nearby_events=context, context_start_position=lo, context_end_position=hi - 1,
        has_earlier_events=lo > 0, has_later_events=hi < len(compact.events))
