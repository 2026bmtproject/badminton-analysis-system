"""Synthetic grounding and structured-output boundaries."""
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from modules.commentary.analysis.tactical_analyzer import (
    analyze_tactical_facts, TacticalAnalysisError, TacticalAnalyzerResult, PROMPT_PATH,
    _validated_inputs,
)
from modules.commentary.facts.schemas import CompactRallyFacts, CompactCourtPositionFact
from modules.commentary.providers.fake import FakeProvider
from modules.commentary.providers.base import ProviderError
from modules.commentary.schemas import Probability, NonNegativeFloat


@pytest.fixture
def compact():
    return CompactRallyFacts.model_validate(dict(
        schema_version="compact-rally-facts-v1", segment_index=1, fps=30,
        start_frame=30, end_frame=90, start_sec=1, end_sec=3,
        score={"a":None,"b":None}, server=None, warnings=[],
        events=[dict(fact_id=f"rally:1:stroke:{i}", event_index=i, frame=frame,
            time_sec=frame/30, player=player, stroke_type=stroke, stroke_confidence=.9,
            pose=None, court_position=None, shuttle_path=None, warnings=[])
            for i, frame, player, stroke in [(4,30,"a","小球"),(5,45,"b","高遠球"),(6,60,"a","殺球")]]))


def proposal(indices=(4,5), **changes):
    return dict(pattern_type="notable_stroke_sequence", start_event_index=indices[0],
        end_event_index=indices[-1], players=["a","b"],
        evidence_fact_ids=[f"rally:1:stroke:{i}" for i in indices]) | changes


def run(compact, facts, **kwargs):
    provider = FakeProvider(json.dumps(dict(segment_index=1, facts=facts)))
    result = analyze_tactical_facts(provider=provider, compact_facts=compact, **kwargs)
    assert len(provider.calls) == 1
    return result, provider


def test_grounded_roundtrip_and_stable_order(compact):
    first, provider = run(compact, [proposal((5,6)), proposal()])
    second, _ = run(compact, [proposal(), proposal((5,6))])
    assert first.facts == second.facts
    assert [f.start_event_index for f in first.facts] == [4,5]
    assert first.facts[0].evidence_fact_ids == ["rally:1:stroke:4","rally:1:stroke:5"]
    assert TacticalAnalyzerResult.model_validate_json(first.model_dump_json()) == first
    payload = json.loads(provider.calls[0].user_prompt)
    assert len(payload["compact_rally_facts"]["events"]) == 3
    assert "score" not in payload["compact_rally_facts"]


@pytest.mark.parametrize("changes,reason", [
    ({"evidence_fact_ids":["rally:2:stroke:4","rally:1:stroke:5"]},"unknown_evidence_id"),
    ({"end_event_index":99},"event_range_mismatch"),
    ({"start_event_index":9},"event_range_mismatch"),
    ({"players":["a"]},"player_association_mismatch"),
    ({"evidence_fact_ids":["rally:1:stroke:5","rally:1:stroke:4"]},"evidence_order_mismatch"),
    ({"pattern_type":"sustained_attack"},"deterministic_pattern_support_mismatch"),
])
def test_invalid_grounding_filtered(compact, changes, reason):
    result, _ = run(compact, [proposal(**changes)])
    assert result.facts == []
    assert any(reason in w for w in result.warnings)


@pytest.mark.parametrize("changes", [{"stroke_confidence":.1},{"stroke_confidence":.6},
    {"stroke_confidence":None},{"player":None},{"stroke_type":None},{"stroke_type":"invented"}])
def test_weak_support_retained_only_as_context(compact, changes):
    compact.events[0] = compact.events[0].model_copy(update=changes)
    result, provider = run(compact, [proposal()])
    assert not result.facts
    assert len(json.loads(provider.calls[0].user_prompt)["compact_rally_facts"]["events"]) == 3


def test_duplicate_and_empty(compact):
    result, _ = run(compact, [proposal(),proposal(players=["b","a"])])
    assert len(result.facts) == 1
    assert any("duplicate_candidate" in w for w in result.warnings)
    assert run(compact, [])[0].facts == []


def test_existing_pattern_support(compact):
    result, _ = run(compact, [proposal((5,6), pattern_type="attack_transition")])
    assert len(result.facts) == 1
    assert result.facts[0].confidence == .9
    assert "not_tactical_probability" in result.facts[0].limitations[1]


@pytest.mark.parametrize("changes", [{"players":["a","a"]},
    {"evidence_fact_ids":["rally:1:stroke:4"]},
    {"evidence_fact_ids":["rally:1:stroke:4","rally:1:stroke:4"]}])
def test_invalid_candidate_structure(compact, changes):
    with pytest.raises(TacticalAnalysisError):
        run(compact, [proposal(**changes)])


@pytest.mark.parametrize("text", ["{", "{}", "[]", '{"segment_index":1,"facts":[]} trailing',
    '{"segment_index":1,"segment_index":1,"facts":[]}', '{"segment_index":NaN,"facts":[]}',
    '{"segment_index":2,"facts":[]}', '{"segment_index":true,"facts":[]}'])
def test_malformed_structured_response(compact, text):
    with pytest.raises(TacticalAnalysisError):
        analyze_tactical_facts(provider=FakeProvider(text), compact_facts=compact)


@pytest.mark.parametrize("forbidden", ["forehand", "backhand", "winner", "intent", "shuttle_speed", "score", "trajectory"])
def test_forbidden_claim_contract(compact, forbidden):
    for changes in ({"pattern_type":forbidden}, {"description":forbidden}):
        with pytest.raises(TacticalAnalysisError):
            run(compact, [proposal(**changes)])


def test_provider_failure_propagation(compact):
    for provider in (FakeProvider(" "), FakeProvider("", error=ProviderError("test","failure"))):
        with pytest.raises(ProviderError):
            analyze_tactical_facts(provider=provider, compact_facts=compact)


def test_analysis_correspondence(compact):
    _, analysis = _validated_inputs(compact, None)
    assert run(compact, [proposal()], deterministic_analysis=analysis)[0].facts
    analysis.segment_index = 9
    with pytest.raises(TacticalAnalysisError, match="does not match"):
        run(compact, [], deterministic_analysis=analysis)


@pytest.mark.parametrize("changes", [{"fact_id":"rally:99:stroke:4"}, {"time_sec":1.01}, {"event_index":5}])
def test_bad_input_before_provider(compact, changes):
    compact.events[0] = compact.events[0].model_copy(update=changes)
    provider = FakeProvider('{}')
    with pytest.raises(TacticalAnalysisError):
        analyze_tactical_facts(provider=provider, compact_facts=compact)
    assert not provider.calls


@pytest.mark.parametrize("quality,accepted", [("reliable",True),("cautious",False)])
def test_court_support_trust(compact, quality, accepted):
    for event, zone, y in [(compact.events[0],"rear",.9),(compact.events[2],"front",.1)]:
        event.court_position = CompactCourtPositionFact(
            fact_id=event.fact_id+":court", source_frame=event.frame, quality=quality,
            position_source="ankles_midpoint", court_x_m=3, court_y_m=y*13.41,
            normalized_x=.5, normalized_y=y, depth_zone=zone, width_zone="center",
            displacement_from_previous_hit_m=None, limitations=[])
    result, _ = run(compact, [proposal((4,6), pattern_type="front_back_court_displacement",
        players=["a"], evidence_fact_ids=["rally:1:stroke:4:court","rally:1:stroke:6:court"])])
    assert bool(result.facts) is accepted


@pytest.mark.parametrize("alias,value", [(Probability,True),(Probability,"0.9"),(NonNegativeFloat,"1.0")])
def test_numeric_aliases_stay_strict(alias, value):
    with pytest.raises(ValidationError):
        TypeAdapter(alias).validate_python(value)


def test_prompt_constraints():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    for phrase in ["Unknown means unknown", "forehand/backhand", "player identity", "scoring cause",
                   "player intent", "shuttle speed", "Low-confidence", "structured response schema", "Empty facts []"]:
        assert phrase in prompt


@pytest.mark.parametrize("indices", [(1,0), (104,97,130)])
def test_builder_chronology_not_numeric_identity(indices):
    from modules.contracts import Segment, HitEvent, StrokeLabel
    from modules.commentary.adapters.upstream import UpstreamStageData
    from modules.commentary.facts.builder import build_compact_rally_facts
    from modules.commentary.identity import CourtPositionToPlayer

    events = [HitEvent(0) for _ in range(max(indices) + 1)]
    strokes = []
    for position, index in enumerate(indices):
        frame = 30 + position * 15
        events[index] = HitEvent(frame)
        strokes.append(StrokeLabel(index, frame, 1, "top" if position % 2 == 0 else "bottom", "小球", .9))
    stages = UpstreamStageData(segments=[Segment(0,29,0,.967,.967), Segment(30,90,1,3,2)],
        fps=30, events=events, strokes=strokes, scores=[], shuttle_method=None)
    compact = build_compact_rally_facts(stages=stages, segment_index=1,
        court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"))
    assert [e.event_index for e in compact.events] == list(indices)
    candidates = [proposal(indices[:2])]
    if len(indices) == 3:
        candidates.append(proposal(indices[1:]))
    first, _ = run(compact, candidates)
    second, _ = run(compact, list(reversed(candidates)))
    assert first.facts == second.facts
    assert [(f.start_event_index, f.end_event_index) for f in first.facts] == [
        (p["start_event_index"], p["end_event_index"]) for p in candidates]
    assert first.facts[0].evidence_fact_ids == [f"rally:1:stroke:{i}" for i in indices[:2]]
    assert TacticalAnalyzerResult.model_validate_json(first.model_dump_json()) == first
    assert run(compact, [])[0].facts == []
    reversed_evidence, _ = run(compact, [proposal(tuple(reversed(indices[:2])))])
    assert not reversed_evidence.facts
    assert any("evidence_order_mismatch" in w for w in reversed_evidence.warnings)
    reversed_endpoints, _ = run(compact, [proposal(indices[:2],
        start_event_index=indices[1], end_event_index=indices[0])])
    assert not reversed_endpoints.facts
    assert any("event_range_mismatch" in w for w in reversed_endpoints.warnings)
