"""Offline v2 boundaries. Fake verdicts test gating, not LLM semantic accuracy."""

import json
import subprocess
import sys
from contextlib import nullcontext

import pytest
from pydantic import ValidationError

from modules.commentary.analysis.observation_analyzer import (
    analyze_tactical_observations, ObservationAnalysisError, ObservationAnalysisResult, PROMPT_DIR,
    GENERATOR_VERSION, REVIEWER_VERSION,
)
from modules.commentary.analysis.observation_response import ObservationResponse, ReviewResponse
from modules.commentary.facts.schemas import CompactRallyFacts, CompactCourtPositionFact
from modules.commentary.providers.base import ProviderDiagnostics, ProviderError, TokenUsage
from modules.commentary.providers.fake import FakeProvider


@pytest.fixture
def compact():
    return CompactRallyFacts.model_validate(dict(
        schema_version="compact-rally-facts-v1", segment_index=1, fps=30,
        start_frame=30, end_frame=90, start_sec=1, end_sec=3,
        score={"a": None, "b": None}, server=None, warnings=[],
        events=[dict(fact_id=f"rally:1:stroke:{i}", event_index=i, frame=frame,
            time_sec=frame/30, player=player, stroke_type="小球", stroke_confidence=.9,
            pose=None, court_position=None, shuttle_path=None, warnings=[])
            for i, frame, player in [(4,30,"a"),(5,45,"b"),(6,60,"a")]]))


def proposal(indices=(4, 5), text="A 與 B 在所引兩拍都使用小球，呈現網前球種的呼應。"):
    return dict(observation=text, supporting_claims=[dict(fact_id=f"rally:1:stroke:{i}", field="stroke_type")
                                                    for i in indices])


class ReviewFake(FakeProvider):
    """Echo only candidate IDs into a predetermined offline verdict."""

    def __init__(self, verdict="pass", codes=(), transform=None):
        super().__init__("", model="offline-review", usage=TokenUsage(input_tokens=12, thought_tokens=3))
        self.verdict, self.codes, self.transform = verdict, list(codes), transform

    def generate(self, **kwargs):
        rows = [dict(candidate_id=c["candidate_id"], verdict=self.verdict, violation_codes=self.codes)
                for c in json.loads(kwargs["user_prompt"])["candidates"]]
        if self.transform:
            rows = self.transform(rows)
        self.response = json.dumps(dict(verdicts=rows))
        return super().generate(**kwargs)


def run(compact, proposals, reviewer=None):
    generator = FakeProvider(json.dumps(dict(observations=proposals)), model="offline-generation")
    reviewer = reviewer or ReviewFake()
    result = analyze_tactical_observations(generator=generator, reviewer=reviewer, compact_facts=compact)
    assert len(generator.calls) == 1
    assert len(reviewer.calls) <= 1
    return result, generator, reviewer


def test_open_vocabulary_without_patterns_and_roundtrip(compact):
    from modules.commentary.analysis.tactical_analyzer import _validated_inputs
    assert not _validated_inputs(compact, None)[1].patterns
    result, generator, reviewer = run(compact, [proposal()])
    assert result.outcome == "accepted"
    fact = result.observations[0]
    assert fact.observation == proposal()["observation"]
    assert (fact.start_event_index, fact.end_event_index, fact.players) == (4, 5, ["a", "b"])
    assert fact.grounding_status == "validated" and fact.semantic_review == "passed"
    assert fact.epistemic_status == "model_interpretation"
    assert "grounding_checked_interpretation_not_proven" in fact.limitations
    assert not {"pattern_type", "category", "confidence", "salience"} & fact.model_dump().keys()
    assert fact.supporting_claims[0].source_classifier_confidence == .9
    assert result.generation.returned_model == "offline-generation"
    assert result.review.usage.thought_tokens == 3
    assert result.review.latency_seconds >= 0
    assert ObservationAnalysisResult.model_validate_json(result.model_dump_json()) == result
    assert generator.calls[0].response_schema is ObservationResponse
    assert reviewer.calls[0].response_schema is ReviewResponse


@pytest.mark.parametrize("text", ["", " ", "\t", " \n\t ", "球"*241, True, 3])
def test_invalid_observation_text(compact, text):
    with pytest.raises(ObservationAnalysisError):
        run(compact, [proposal(text=text)])


@pytest.mark.parametrize("field,value", [("players", ["a"]), ("segment_index", 1),
    ("confidence", .9), ("grounding_status", "validated"), ("semantic_review", "passed"),
    ("epistemic_status", "model_interpretation"), ("pattern_type", "new-tactic"),
    ("start_event_index", 4), ("fact_id", "invented"), ("limitations", [])])
def test_model_cannot_supply_trusted_fields(compact, field, value):
    with pytest.raises(ObservationAnalysisError):
        run(compact, [proposal() | {field: value}])


@pytest.mark.parametrize("text", ["{", "{}", "[]", '{"observations":[]} trailing',
    '{"observations":[],"observations":[]}', '{"observations":NaN}', '```json\n{}\n```'])
@pytest.mark.parametrize("stage", ["generator", "reviewer"])
def test_malformed_fails_component(compact, text, stage):
    generator = FakeProvider(text if stage == "generator" else json.dumps(dict(observations=[proposal()])))
    reviewer = FakeProvider(text)
    with pytest.raises(ObservationAnalysisError) as caught:
        analyze_tactical_observations(generator=generator, reviewer=reviewer, compact_facts=compact)
    assert (GENERATOR_VERSION if stage == "generator" else REVIEWER_VERSION) == caught.value.stage


@pytest.mark.parametrize("ref,field,reason", [("rally:1:stroke:999", "stroke_type", "unknown_evidence_id"),
    ("rally:2:stroke:4", "stroke_type", "unknown_evidence_id"),
    ("rally:1:stroke:4", "winner", "unsupported_source_field"),
    ("rally:1:stroke:4", "depth_zone", "unsupported_source_field")])
def test_rejected_grounding_skips_review(compact, ref, field, reason):
    p = proposal()
    p["supporting_claims"][0] = dict(fact_id=ref, field=field)
    result, _, reviewer = run(compact, [p])
    assert result.outcome == "grounding_rejected" and not result.observations
    assert result.grounding_rejections[0].reason == reason
    assert not reviewer.calls and result.review is None


@pytest.mark.parametrize("changes", [{"stroke_confidence": .69}, {"stroke_confidence": None},
    {"player": None}, {"stroke_type": None}, {"stroke_type": "fictional"}])
def test_weak_unknown_not_support(compact, changes):
    compact.events[0] = compact.events[0].model_copy(update=changes)
    result, generator, reviewer = run(compact, [proposal()])
    assert result.outcome == "grounding_rejected" and not reviewer.calls
    raw = json.loads(generator.calls[0].user_prompt)["compact_rally_facts"]["events"][0]
    for key, value in changes.items():
        assert raw[key] == value


@pytest.mark.parametrize("value", [True, "0.9", float("inf"), float("nan")])
def test_input_confidence_not_coerced(compact, value):
    from modules.commentary.analysis.tactical_analyzer import TacticalAnalysisError
    compact.events[0].stroke_confidence = value
    generator = FakeProvider('{"observations":[]}')
    warning = pytest.warns(UserWarning, match="serializer warnings") if isinstance(value, str) else nullcontext()
    with warning, pytest.raises(TacticalAnalysisError):
        analyze_tactical_observations(generator=generator, reviewer=ReviewFake(), compact_facts=compact)
    assert not generator.calls


def test_empty_and_insufficient_events(compact):
    result, _, reviewer = run(compact, [])
    assert result.outcome == "generated_empty" and result.raw_proposal_count == 0
    assert not reviewer.calls
    result, _, reviewer = run(compact, [proposal((4,4))])
    assert result.outcome == "grounding_rejected"
    assert result.grounding_rejections[0].reason == "insufficient_evidence_events"
    assert not reviewer.calls


def test_canonical_duplicates_and_stable_ids(compact):
    p = proposal()
    duplicated = proposal((5,4,4))
    different = proposal(text="所引兩拍的小球由不同球員擊出。")
    first, _, _ = run(compact, [proposal((5,6)), duplicated, different, p])
    second, _, _ = run(compact, [p, different, proposal((5,6))])
    assert first.observations == second.observations
    assert len(first.observations) == 3
    assert [r.reason for r in first.grounding_rejections] == ["duplicate_candidate"]
    assert len({o.fact_id for o in first.observations}) == 3
    for o in first.observations:
        assert len(o.evidence_fact_ids) == len(set(o.evidence_fact_ids))


@pytest.mark.parametrize("indices", [(1,0), (104,97,130)])
def test_actual_builder_nonmonotonic_chronology(indices):
    from modules.contracts import Segment, HitEvent, StrokeLabel
    from modules.commentary.adapters.upstream import UpstreamStageData
    from modules.commentary.facts.builder import build_compact_rally_facts
    from modules.commentary.identity import CourtPositionToPlayer
    events = [HitEvent(0) for _ in range(max(indices)+1)]
    strokes = []
    for pos, index in enumerate(indices):
        frame = 30 + pos*15
        events[index] = HitEvent(frame)
        strokes.append(StrokeLabel(index, frame, 1, "top" if pos % 2 == 0 else "bottom", "小球", .9))
    stages = UpstreamStageData(segments=[Segment(0,29,0,.967,.967), Segment(30,90,1,3,2)],
        fps=30, events=events, strokes=strokes, scores=[], shuttle_method=None)
    compact = build_compact_rally_facts(stages=stages, segment_index=1,
        court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"))
    result, _, reviewer = run(compact, [proposal(tuple(reversed(indices)))])
    o = result.observations[0]
    assert [c.event_index for c in o.supporting_claims] == list(indices)
    assert (o.start_event_index, o.end_event_index) == (indices[0], indices[-1])
    assert [e["event_index"] for e in json.loads(reviewer.calls[0].user_prompt)["candidates"][0]["nearby_events"]] == list(indices)


@pytest.mark.parametrize("text,code", [
    ("B 贏得這一回合。", "unsupported_outcome"),
    ("這拍直接替 A 拿下一分。", "unsupported_outcome"),
    ("B 刻意把球送到網前。", "unsupported_intent_or_causality"),
    ("A 的目的就是牽制 B。", "unsupported_intent_or_causality"),
    ("A 讓對手不得不挑高。", "unsupported_intent_or_causality"),
    ("B 以小球迫使 A 失誤。", "unsupported_intent_or_causality"),
    ("A 用正手接著反手回擊。", "unsupported_stroke_side"),
    ("A 的球速超過每小時三百公里。", "unsupported_motion_or_physics"),
    ("羽球在三維空間劃出高拋弧線。", "unsupported_motion_or_physics"),
    ("B 一路從後場奔向網前。", "unsupported_motion_or_physics"),
    ("戴資穎在所引兩拍都打小球。", "unsupported_identity"),
    ("A 連續兩拍打小球。", "text_evidence_mismatch"),
])
def test_valid_atoms_do_not_bypass_semantic_rejection(compact, text, code):
    result, _, reviewer = run(compact, [proposal((4,6), text)], ReviewFake("reject", [code]))
    assert result.outcome == "semantic_rejected" and not result.observations
    assert not result.grounding_rejections and len(reviewer.calls) == 1
    assert result.review_verdicts[0].violation_codes == [code]


def test_uncertain_and_minimal_context(compact):
    # Grow to seven events, cite positions 2 and 4; retain exactly positions 1..5.
    base = compact.events[0]
    compact.end_frame, compact.end_sec = 150, 5
    compact.events = [base.model_copy(update=dict(fact_id=f"rally:1:stroke:{i}", event_index=i,
        frame=30+i*15, time_sec=(30+i*15)/30, stroke_confidence=None if i == 3 else .9)) for i in range(7)]
    result, _, reviewer = run(compact, [proposal((2,4))], ReviewFake("uncertain"))
    assert result.outcome == "semantic_rejected"
    payload = json.loads(reviewer.calls[0].user_prompt)
    c = payload["candidates"][0]
    assert [e["position"] for e in c["nearby_events"]] == [1,2,3,4,5]
    assert c["nearby_events"][2]["source_classifier_confidence"] is None
    assert c["has_earlier_events"] and c["has_later_events"]
    assert all(set(e) == {"position", "event_index", "player", "stroke_type", "source_classifier_confidence"}
               for e in c["nearby_events"])
    assert not {"compact_rally_facts", "score", "pose", "shuttle_path", "optional_pattern_hints"} & payload.keys()


@pytest.mark.parametrize("transform", [lambda rows: [], lambda rows: rows+rows,
    lambda rows: [rows[0] | {"candidate_id":"unknown"}], lambda rows: rows[:1]])
def test_reviewer_must_cover_exact_candidate_set(compact, transform):
    with pytest.raises(ObservationAnalysisError):
        run(compact, [proposal(), proposal((5,6))], ReviewFake(transform=transform))


@pytest.mark.parametrize("stage", ["generator", "reviewer"])
def test_provider_diagnostics_and_partial_content_fail_closed(compact, stage):
    diagnostic = ProviderDiagnostics(finish_reason="MAX_TOKENS", requested_model="test",
        candidate_count=1, text_present=True, parsed_content_present=True,
        usage=TokenUsage(thought_tokens=40))
    error = ProviderError("incomplete_response", "Incomplete", diagnostics=diagnostic)
    bad = FakeProvider(json.dumps(dict(observations=[proposal()])), error=error)
    generator = bad if stage == "generator" else FakeProvider(json.dumps(dict(observations=[proposal()])))
    reviewer = bad if stage == "reviewer" else ReviewFake()
    with pytest.raises(ProviderError) as caught:
        analyze_tactical_observations(generator=generator, reviewer=reviewer, compact_facts=compact)
    assert caught.value is error and caught.value.diagnostics is diagnostic
    assert len(bad.calls) == 1
    if stage == "generator":
        assert not reviewer.calls


@pytest.mark.parametrize("quality,accepted", [("reliable", True), ("cautious", False)])
@pytest.mark.parametrize("field", ["depth_zone", "width_zone"])
def test_reliable_court_capabilities(compact, quality, accepted, field):
    for e in compact.events:
        e.court_position = CompactCourtPositionFact(fact_id=e.fact_id+":court", source_frame=e.frame,
            quality=quality, position_source="ankles_midpoint", court_x_m=3, court_y_m=3,
            normalized_x=.5, normalized_y=.2, depth_zone="front", width_zone="center",
            displacement_from_previous_hit_m=None, limitations=[])
    p = proposal()
    p["supporting_claims"] = [dict(fact_id=e.fact_id+":court", field=field) for e in compact.events[:2]]
    result, _, reviewer = run(compact, [p])
    assert bool(result.observations) is accepted
    assert bool(reviewer.calls) is accepted


def test_bounds_and_strict_wire_fields():
    for data in [dict(observations=[proposal()]*6), dict(observations=[proposal((4,))]),
                 dict(observations=[proposal(tuple(range(13)))]),
                 dict(observations=[dict(observation="球", supporting_claims=[dict(fact_id=1, field=True)]*2)])]:
        with pytest.raises(ValidationError):
            ObservationResponse.model_validate(data)


def test_prompts_agree_with_boundary():
    g = (PROMPT_DIR / f"{GENERATOR_VERSION}.txt").read_text(encoding="utf-8")
    r = (PROMPT_DIR / f"{REVIEWER_VERSION}.txt").read_text(encoding="utf-8")
    for phrase in ["Traditional Chinese", "optional hints", "forehand/backhand", "intent", "3D", "Unknown"]:
        assert phrase in g
    for phrase in ["exactly one", "context only", "every event", "uncertain", "not proof", "untrusted"]:
        assert phrase in r


def test_provider_agnostic_import_without_gemini():
    code = """
import sys
class BlockGemini:
    def find_spec(self, fullname, *args):
        if fullname == 'google.genai' or fullname.startswith('google.genai.'):
            raise ImportError('SDK deliberately unavailable')
sys.meta_path.insert(0, BlockGemini())
from modules.commentary.analysis.observation_analyzer import analyze_tactical_observations
assert 'google.genai' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_mixed_batch_reviews_and_single_provider(compact):
    class TwoRoleFake(FakeProvider):
        def generate(self, **kwargs):
            if not self.calls:
                self.response = json.dumps(dict(observations=[proposal(), proposal((5,6))]))
            else:
                assert len(self.calls) == 1  # No retries or per-observation review.
                candidates = json.loads(kwargs["user_prompt"])["candidates"]
                self.response = json.dumps(dict(verdicts=[dict(candidate_id=c["candidate_id"],
                    verdict="pass" if i == 0 else "uncertain", violation_codes=[])
                    for i, c in reversed(list(enumerate(candidates)))]))
            return super().generate(**kwargs)
    provider = TwoRoleFake("")
    result = analyze_tactical_observations(generator=provider, reviewer=provider, compact_facts=compact)
    assert len(provider.calls) == 2
    assert len(result.observations) == 1 and result.outcome == "accepted"
    assert [v.verdict for v in result.review_verdicts] == ["pass", "uncertain"]


@pytest.mark.parametrize("changes", [{"verdict":"pass", "violation_codes":["unsupported_outcome"]},
    {"verdict":"reject", "violation_codes":[]}, {"verdict":"rewrite"},
    {"violation_codes":["invented"]}, {"extra":"text"}])
def test_inconsistent_review_contract(compact, changes):
    with pytest.raises(ObservationAnalysisError):
        run(compact, [proposal()], ReviewFake(transform=lambda rows: [rows[0] | changes]))


@pytest.mark.parametrize("field", ["winner", "intent", "speed", "forehand", "trajectory", "stroke_quality", "time_sec"])
def test_no_forbidden_resolver(compact, field):
    p = proposal()
    p["supporting_claims"][0]["field"] = field
    result, _, reviewer = run(compact, [p])
    assert result.outcome == "grounding_rejected" and not reviewer.calls


def test_source_values_cannot_be_generated(compact):
    p = proposal()
    p["supporting_claims"][0]["value"] = "殺球"
    with pytest.raises(ObservationAnalysisError):
        run(compact, [p])


@pytest.mark.parametrize("text", ["", " \t"])
@pytest.mark.parametrize("phase", ["generator", "reviewer"])
def test_empty_provider_is_not_empty_success(compact, text, phase):
    generator = FakeProvider(text if phase == "generator" else json.dumps(dict(observations=[proposal()])))
    reviewer = FakeProvider(text)
    with pytest.raises(ProviderError, match="no structured response"):
        analyze_tactical_observations(generator=generator, reviewer=reviewer, compact_facts=compact)
