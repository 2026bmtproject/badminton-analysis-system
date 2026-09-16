"""Offline Commentator/CommentaryService contract and safety tests."""

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from modules.commentary.analysis.observation_analyzer import analyze_tactical_observations
from modules.commentary.facts.observations import TacticalObservation
from modules.commentary.facts.schemas import CompactRallyFacts
from modules.commentary.facts.builder import build_compact_rally_facts
from modules.commentary.generation.commentator import (
    CommentaryGenerationError,
    PROMPT_PATH,
    _HIDDEN_FINE_STROKE_LABELS,
    _validate_text,
)
from modules.commentary.generation.planner import CommentaryPlanningError, build_commentary_plan
from modules.commentary.generation.response import CommentatorResponse
from modules.commentary.generation.review_response import CommentaryReviewResponse
from modules.commentary.generation.reviewer import (
    CommentarySemanticReviewError,
    REVIEW_PROMPT_PATH,
)
from modules.commentary.identity import CourtPositionToPlayer
from modules.commentary.providers.base import ProviderDiagnostics, ProviderError, TokenUsage
from modules.commentary.providers.fake import FakeProvider
from modules.commentary.schemas import RallyFact, RallyScore
from modules.commentary.services import CommentaryService


@pytest.fixture
def facts():
    events = [
        dict(event_index=104, frame=30, time_sec=1.0, player="a",
             stroke_type="小球", stroke_confidence=.9),
        dict(event_index=97, frame=45, time_sec=1.5, player="b",
             stroke_type="高遠球", stroke_confidence=.8),
        dict(event_index=130, frame=60, time_sec=2.0, player=None,
             stroke_type="切球", stroke_confidence=.95),
        dict(event_index=205, frame=75, time_sec=2.5, player="a",
             stroke_type="殺球", stroke_confidence=.3),
    ]
    rally = RallyFact(
        segment_index=7, game_index=None, start_sec=1.0, end_sec=3.0,
        duration_sec=2.0, score={"a": None, "b": 4}, server=None,
        events=events, rally_length=4, highlight_score=0.0,
    )
    compact = CompactRallyFacts.model_validate(dict(
        schema_version="compact-rally-facts-v1", segment_index=7, fps=30.0,
        start_frame=30, end_frame=90, start_sec=1.0, end_sec=3.0,
        score={"a": None, "b": 4}, server=None, warnings=[],
        events=[dict(
            fact_id=f"rally:7:stroke:{event['event_index']}", **event,
            pose=None, court_position=None, shuttle_path=None, warnings=[],
        ) for event in events],
    ))
    return rally, compact


def response(*, indexes=(104, 97, 205), summary="這段來回呈現多種擊球的接續。"):
    texts = {
        104: "球員 a 以小球展開這拍交換。",
        97: "球員 b 接著打出高遠球。",
        205: "辨識結果看來，球員 a 可能以殺球回擊。",
    }
    return json.dumps(dict(
        events=[dict(event_index=index, text=texts.get(index, "球員 a 完成回擊。"))
                for index in indexes],
        summary=summary,
    ), ensure_ascii=False)


def service_result(facts, text=None, *, observations=None, include_summary=True):
    rally, compact = facts
    provider = FakeProvider(text or response(), model="offline-commentator",
                            usage=TokenUsage(input_tokens=20, output_tokens=10))
    reviewer = PassCommentaryReview("", model="offline-reviewer",
                                    usage=TokenUsage(input_tokens=12, output_tokens=5))
    result = CommentaryService(
        provider=provider, reviewer=reviewer,
        requested_model="requested-offline", reviewer_requested_model="review-offline",
    ).generate(
        rally_fact=rally, compact_facts=compact,
        court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
        tactical_observations=observations, include_summary=include_summary,
    )
    provider.semantic_reviewer = reviewer
    return result, provider


class EchoReview(FakeProvider):
    def generate(self, **kwargs):
        candidates = json.loads(kwargs["user_prompt"])["candidates"]
        self.response = json.dumps(dict(verdicts=[
            dict(candidate_id=item["candidate_id"], verdict="pass", violation_codes=[])
            for item in candidates
        ]))
        return super().generate(**kwargs)


class PassCommentaryReview(FakeProvider):
    def generate(self, **kwargs):
        payload = json.loads(kwargs["user_prompt"])
        self.response = json.dumps(dict(
            events=[dict(event_index=item["event_index"], verdict="pass", violation_codes=[])
                    for item in payload["events"]],
            summary=(dict(verdict="pass", violation_codes=[])
                     if payload["summary"] is not None else None),
        ))
        return super().generate(**kwargs)


def accepted_observation(compact):
    proposal = dict(observation="球員 a 的小球後接球員 b 的高遠球。", supporting_claims=[
        dict(fact_id="rally:7:stroke:104", field="stroke_type"),
        dict(fact_id="rally:7:stroke:97", field="stroke_type"),
    ])
    result = analyze_tactical_observations(
        generator=FakeProvider(json.dumps(dict(observations=[proposal]), ensure_ascii=False)),
        reviewer=EchoReview(""), compact_facts=compact,
    )
    return result.observations[0]


def test_one_call_complete_coverage_and_canonical_join(facts):
    result, provider = service_result(facts, response(indexes=(205, 104, 97)))
    assert len(provider.calls) == 1 and provider.calls[0].response_schema is CommentatorResponse
    assert result.generation.provider_calls == 1
    assert result.generation.requested_model == "requested-offline"
    assert result.generation.returned_model == "offline-commentator"
    assert result.generation.usage.input_tokens == 20
    assert result.eligible_event_indices == [104, 97, 205]
    events = result.commentary.events
    assert [event.stroke_index for event in events] == [104, 97, 205]
    assert [(event.frame, event.time_sec) for event in events] == [(30, 1.0), (45, 1.5), (75, 2.5)]
    assert [event.source_fact_ids for event in events] == [
        ["rally:7:stroke:104"], ["rally:7:stroke:97"], ["rally:7:stroke:205"],
    ]
    assert result.commentary.summary is not None
    reviewer = provider.semantic_reviewer
    assert len(reviewer.calls) == 1
    assert reviewer.calls[0].response_schema is CommentaryReviewResponse
    assert result.semantic_review.provider_calls == 1
    assert result.semantic_review.requested_model == "review-offline"
    assert result.semantic_review.returned_model == "offline-reviewer"
    assert result.semantic_review.usage.input_tokens == 12
    assert not result.summary_omitted_by_review


def test_no_tactics_and_context_retains_unknown_and_low_confidence(facts):
    result, provider = service_result(facts)
    payload = json.loads(provider.calls[0].user_prompt)
    assert payload["tactical_observations"] == []
    assert [event["event_index"] for event in payload["events"]] == [104, 97, 130, 205]
    unknown = payload["events"][2]
    assert unknown["player"] is None and not unknown["eligible_for_output"]
    low = payload["events"][3]
    assert low["source_classifier_confidence"] == .3 and low["confidence_band"] == "low"
    assert result.commentary.events[-1].stroke_index == 205


def test_accepted_tactical_observation_revalidated_and_retained(facts):
    rally, compact = facts
    observation = accepted_observation(compact)
    result, provider = service_result(facts, observations=[observation])
    assert result.supplied_tactical_observation_ids == [observation.fact_id]
    payload = json.loads(provider.calls[0].user_prompt)
    assert payload["tactical_observations"][0]["fact_id"] == observation.fact_id
    assert payload["events"][0]["stroke_type"] == "小球"
    assert result.commentary.events[0].source_fact_ids == ["rally:7:stroke:104"]


def test_unreviewed_or_tampered_tactical_input_rejected_before_call(facts):
    rally, compact = facts
    provider = FakeProvider(response())
    service = CommentaryService(provider=provider, reviewer=FakeProvider("unused"))
    with pytest.raises(CommentaryPlanningError, match="invalid tactical"):
        service.generate(rally_fact=rally, compact_facts=compact,
            court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
            tactical_observations=[{"observation": "unreviewed"}])
    observation = accepted_observation(compact)
    tampered = observation.model_copy(update={"supporting_claims": [
        observation.supporting_claims[0].model_copy(update={"value": "殺球"}),
        observation.supporting_claims[1],
    ]})
    with pytest.raises(CommentaryPlanningError, match="provenance"):
        service.generate(rally_fact=rally, compact_facts=compact,
            court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
            tactical_observations=[tampered])
    bad_status = observation.model_copy(update={"semantic_review": "not_reviewed"})
    with pytest.raises(CommentaryPlanningError, match="invalid tactical"):
        service.generate(rally_fact=rally, compact_facts=compact,
            court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
            tactical_observations=[bad_status])
    assert not provider.calls


@pytest.mark.parametrize("indexes", [(104, 97), (104, 97, 205, 999)])
def test_partial_or_unknown_event_coverage_fails(facts, indexes):
    with pytest.raises(CommentaryGenerationError, match="exactly cover"):
        service_result(facts, response(indexes=indexes))


@pytest.mark.parametrize("payload", [
    {"events": [
        {"event_index": 104, "text": "文字"}, {"event_index": 104, "text": "文字"},
        {"event_index": 97, "text": "文字"}, {"event_index": 205, "text": "可能是這拍。"},
    ], "summary": "摘要"},
    {"events": [
        {"event_index": 104, "text": "文字", "player": "a"},
        {"event_index": 97, "text": "文字"}, {"event_index": 205, "text": "可能是這拍。"},
    ], "summary": "摘要"},
    {"events": [
        {"event_index": 104, "text": "文字"}, {"event_index": 97, "text": "文字"},
        {"event_index": 205, "text": "可能是這拍。"},
    ], "summary": "摘要", "segment_index": 7},
])
def test_duplicate_or_model_authored_trusted_fields_fail(facts, payload):
    with pytest.raises(CommentaryGenerationError):
        service_result(facts, json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize("text", ["", " ", "\t", " \n\t "])
def test_blank_commentary_fails(facts, text):
    data = json.loads(response())
    data["events"][0]["text"] = text
    with pytest.raises(CommentaryGenerationError):
        service_result(facts, json.dumps(data, ensure_ascii=False))


@pytest.mark.parametrize("text", ["{", "[]", "{}", '{"events":NaN,"summary":null}',
                                     '{"events":[],"events":[],"summary":null}'])
def test_malformed_structured_response_fails_closed(facts, text):
    with pytest.raises(CommentaryGenerationError):
        service_result(facts, text)


def test_incomplete_provider_error_propagates(facts):
    rally, compact = facts
    diagnostic = ProviderDiagnostics(
        finish_reason="MAX_TOKENS", requested_model="test", candidate_count=1,
        text_present=True, parsed_content_present=False,
    )
    error = ProviderError("incomplete_response", "incomplete", diagnostics=diagnostic)
    provider = FakeProvider("", error=error)
    with pytest.raises(ProviderError) as caught:
        CommentaryService(provider=provider, reviewer=FakeProvider("unused")).generate(
            rally_fact=rally, compact_facts=compact,
            court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
        )
    assert caught.value is error and caught.value.diagnostics is diagnostic
    assert len(provider.calls) == 1


@pytest.mark.parametrize(("bad", "code"), [
    ("球員 a 拿下這一分。", "unsupported_outcome_or_score"),
    ("球員 a 正手殺球。", "unsupported_stroke_side"),
    ("球速達到每小時三百公里。", "unsupported_motion_or_physics"),
    ("球員 a 一路跑向網前。", "unsupported_motion_or_physics"),
    ("球員 a 心態緊張。", "unsupported_psychology"),
    ("目前比分是 4 比 3。", "unsupported_outcome_or_score"),
    ("球員 a 的調動造成對手失誤。", "unsupported_outcome_or_score"),
])
def test_obvious_forbidden_generated_wording_uses_safe_fallback(facts, bad, code):
    data = json.loads(response())
    data["events"][0]["text"] = bad
    result, _ = service_result(facts, json.dumps(data, ensure_ascii=False))
    assert result.commentary.events[0].text == "球員 a 以小球回擊。"
    diagnostic = result.event_output_diagnostics[0]
    assert diagnostic.reviewer_verdict == "pass"
    assert diagnostic.deterministic_gate == "failed"
    assert diagnostic.deterministic_violation_code == code
    assert diagnostic.text_source == "deterministic_fallback"


def test_low_confidence_requires_cautious_wording(facts):
    data = json.loads(response())
    data["events"][-1]["text"] = "球員 a 擊出殺球。"
    result, _ = service_result(facts, json.dumps(data, ensure_ascii=False))
    assert result.commentary.events[-1].text == "球員 a 這拍可能以殺球回擊。"
    diagnostic = result.event_output_diagnostics[-1]
    assert diagnostic.reviewer_verdict == "pass"
    assert diagnostic.deterministic_gate == "failed"
    assert diagnostic.deterministic_violation_code == "missing_cautious_wording"
    assert diagnostic.text_source == "deterministic_fallback"


def test_look_like_marker_satisfies_cautious_wording(facts):
    data = json.loads(response())
    data["events"][-1]["text"] = "球員 a 看似以殺球回擊。"
    result, _ = service_result(facts, json.dumps(data, ensure_ascii=False))
    assert result.commentary.events[-1].text == "球員 a 看似以殺球回擊。"


@pytest.mark.parametrize("include_summary,summary", [(True, None), (False, "不應存在的摘要。")])
def test_summary_presence_must_match_plan(facts, include_summary, summary):
    data = json.loads(response(summary=summary))
    with pytest.raises(CommentaryGenerationError, match="summary presence"):
        service_result(facts, json.dumps(data, ensure_ascii=False), include_summary=include_summary)


@pytest.mark.parametrize("highlight", [None, 0.0, 1.0])
def test_highlight_context_never_gates_events(facts, highlight):
    rally, compact = facts
    rally = rally.model_copy(update={"highlight_score": highlight})
    result, provider = service_result((rally, compact))
    payload = json.loads(provider.calls[0].user_prompt)
    assert result.eligible_event_indices == [104, 97, 205]
    if highlight is None:
        assert payload["highlight_context"] is None
    else:
        assert payload["highlight_context"]["ranking_score"] == highlight
        assert "not_probability" in payload["highlight_context"]["semantics"]


@pytest.mark.parametrize("score", [{"a": None, "b": None}, {"a": 3, "b": None},
                                     {"a": None, "b": 4}])
def test_absent_or_incomplete_score_is_withheld(facts, score):
    rally, compact = facts
    score = RallyScore(**score)
    rally = rally.model_copy(update={"score": score})
    compact = compact.model_copy(update={"score": score})
    _, provider = service_result((rally, compact))
    payload = json.loads(provider.calls[0].user_prompt)
    assert payload["score_context"] == "withheld_no_outcome_inference"
    assert "score" not in payload and "server" not in payload


def test_no_summary_option_and_zero_eligible_call_policy(facts):
    data = json.loads(response(summary=None))
    result, provider = service_result(facts, json.dumps(data, ensure_ascii=False), include_summary=False)
    assert result.commentary.summary is None and len(provider.calls) == 1
    rally, compact = facts
    rally = rally.model_copy(update={"events": [e.model_copy(update={"player": None}) for e in rally.events]})
    compact = compact.model_copy(update={"events": [e.model_copy(update={"player": None}) for e in compact.events]})
    provider = FakeProvider("must not be used")
    reviewer = FakeProvider("must not be used")
    result = CommentaryService(provider=provider, reviewer=reviewer).generate(
        rally_fact=rally, compact_facts=compact,
        court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
    )
    assert result.commentary.events == [] and result.commentary.summary is None
    assert result.generation.provider_calls == 0 and not provider.calls
    assert result.semantic_review.provider_calls == 0 and not reviewer.calls


def test_explicit_and_reversed_mapping_are_preserved_in_plan(facts):
    rally, compact = facts
    normal = build_commentary_plan(rally_fact=rally, compact_facts=compact,
        court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"))
    reversed_plan = build_commentary_plan(rally_fact=rally, compact_facts=compact,
        court_position_to_player=CourtPositionToPlayer(top="b", bottom="a"))
    assert normal.identity_mapping.model_dump() == {"top": "a", "bottom": "b"}
    assert reversed_plan.identity_mapping.model_dump() == {"top": "b", "bottom": "a"}
    assert [e.player for e in normal.events] == [e.player for e in reversed_plan.events]


def test_actual_builder_applies_explicit_mapping_in_both_directions():
    from modules.commentary.adapters.upstream import UpstreamStageData, build_rally_fact_from_stages
    from modules.contracts import HitEvent, Segment, StrokeLabel

    stages = UpstreamStageData(
        segments=[Segment(30, 60, 1.0, 2.0, 1.0)], fps=30,
        events=[HitEvent(30), HitEvent(45)],
        strokes=[
            StrokeLabel(0, 30, 0, "top", "小球", .9),
            StrokeLabel(1, 45, 0, "bottom", "高遠球", .9),
        ], scores=[],
    )
    players = []
    for mapping in (CourtPositionToPlayer(top="a", bottom="b"),
                    CourtPositionToPlayer(top="b", bottom="a")):
        rally = build_rally_fact_from_stages(
            stages=stages, segment_index=0, court_position_to_player=mapping)
        compact = build_compact_rally_facts(
            stages=stages, segment_index=0, court_position_to_player=mapping)
        plan = build_commentary_plan(
            rally_fact=rally, compact_facts=compact, court_position_to_player=mapping)
        players.append([event.player for event in plan.events])
    assert players == [["a", "b"], ["b", "a"]]


@pytest.mark.parametrize("quality,visible", [("reliable", True), ("cautious", False)])
def test_only_reliable_court_zones_reach_plan(facts, quality, visible):
    from modules.commentary.facts.schemas import CompactCourtPositionFact

    rally, compact = facts
    compact.events[0].court_position = CompactCourtPositionFact(
        fact_id="rally:7:stroke:104:court", source_frame=30, quality=quality,
        position_source="ankles_midpoint" if quality == "reliable" else "bbox_bottom_center",
        court_x_m=3.0, court_y_m=3.0, normalized_x=.5, normalized_y=.2,
        depth_zone="rear", width_zone="center",
        displacement_from_previous_hit_m=None, limitations=["sampled_hit_position_only"],
    )
    plan = build_commentary_plan(
        rally_fact=rally, compact_facts=compact,
        court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
    )
    assert (plan.events[0].court_zone is not None) is visible
    if visible:
        assert plan.events[0].court_zone.depth_zone == "rear"


def test_planning_and_joining_are_deterministic(facts):
    rally, compact = facts
    mapping = CourtPositionToPlayer(top="a", bottom="b")
    first = build_commentary_plan(
        rally_fact=rally, compact_facts=compact, court_position_to_player=mapping)
    second = build_commentary_plan(
        rally_fact=rally, compact_facts=compact, court_position_to_player=mapping)
    assert first.model_dump_json() == second.model_dump_json()
    a, _ = service_result(facts)
    b, _ = service_result(facts)
    assert a.commentary == b.commentary


def test_canonical_mismatch_fails_before_provider(facts):
    rally, compact = facts
    compact.events[0] = compact.events[0].model_copy(update={"frame": 31})
    provider = FakeProvider(response())
    with pytest.raises(CommentaryPlanningError, match="canonical event mismatch"):
        CommentaryService(provider=provider, reviewer=FakeProvider("unused")).generate(
            rally_fact=rally, compact_facts=compact,
            court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
        )
    assert not provider.calls


def test_mutated_invalid_trusted_model_fails_before_provider(facts):
    rally, compact = facts
    rally.events[0].stroke_confidence = True
    provider = FakeProvider(response())
    with pytest.raises(CommentaryPlanningError, match="invalid trusted commentary input"):
        CommentaryService(provider=provider, reviewer=FakeProvider("unused")).generate(
            rally_fact=rally, compact_facts=compact,
            court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
        )
    assert not provider.calls


def test_prompt_states_required_safety_boundary():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    for phrase in ["Traditional Chinese", "required_event_indices", "unknown event",
                   "Tactical observations are optional", "not probability", "winner",
                   "forehand/backhand", "3D trajectory", "one concise sentence"]:
        assert phrase in prompt


def test_provider_agnostic_service_import_does_not_load_gemini():
    code = """
import sys
class BlockGemini:
    def find_spec(self, fullname, *args):
        if fullname == 'google.genai' or fullname.startswith('google.genai.'):
            raise ImportError('blocked')
sys.meta_path.insert(0, BlockGemini())
from modules.commentary.services import CommentaryService
assert 'google.genai' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("event_index", [True, "104", 1.5])
def test_wire_event_index_is_strict(event_index):
    with pytest.raises(ValidationError):
        CommentatorResponse.model_validate(dict(
            events=[dict(event_index=event_index, text="文字")], summary="摘要",
        ))


def reviewer_response(*, verdicts=None, summary=("pass", [])):
    verdicts = verdicts or [(104, "pass", []), (97, "pass", []), (205, "pass", [])]
    return json.dumps(dict(
        events=[dict(event_index=index, verdict=verdict, violation_codes=codes)
                for index, verdict, codes in verdicts],
        summary=(dict(verdict=summary[0], violation_codes=summary[1])
                 if summary is not None else None),
    ))


def run_with_reviewer(facts, *, generated=None, reviewed=None, observations=None):
    rally, compact = facts
    provider = FakeProvider(generated or response())
    reviewer = FakeProvider(reviewed or reviewer_response())
    result = CommentaryService(provider=provider, reviewer=reviewer).generate(
        rally_fact=rally, compact_facts=compact,
        court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
        tactical_observations=observations,
    )
    return result, provider, reviewer


def test_reviewer_receives_minimal_canonical_batch_context(facts):
    result, _, reviewer = run_with_reviewer(facts)
    assert len(reviewer.calls) == 1
    payload = json.loads(reviewer.calls[0].user_prompt)
    assert [item["event_index"] for item in payload["events"]] == [104, 97, 205]
    first = payload["events"][0]
    assert first["canonical_event"]["player"] == "a"
    assert first["canonical_event"]["stroke_type"] == "小球"
    assert first["canonical_event"]["source_reliability"] == "reliable"
    assert not first["trusted_court_facts_available"]
    assert [item["event_index"] for item in first["immediate_chronology"]] == [104, 97]
    assert "pose" not in reviewer.calls[0].user_prompt
    assert "shuttle" not in reviewer.calls[0].user_prompt
    assert payload["summary"]["trusted_court_facts_available"] is False
    assert [item["event_index"] for item in payload["summary"]["canonical_stroke_sequence"]] == [104, 97, 130, 205]
    assert len(result.event_review_verdicts) == 3


@pytest.mark.parametrize("text", [
    "球員 a 打出小球。",
    "球員 a 打出小球後，球員 b 接著以高遠球回擊。",
    "球員 a 順勢變換節奏，球員 b 接續展開進攻。",
    "球員 a 調動節奏後，球員 b 抓準機會壓上。",
])
def test_semantic_reviewer_pass_accepts_grounded_prose(facts, text):
    generated = json.loads(response())
    generated["events"][0]["text"] = text
    result, _, reviewer = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False))
    assert result.commentary.events[0].text == text
    diagnostic = result.event_output_diagnostics[0]
    assert diagnostic.reviewer_verdict == "pass"
    assert diagnostic.deterministic_gate == "passed"
    assert diagnostic.deterministic_violation_code is None
    assert diagnostic.text_source == "generated"
    assert len(reviewer.calls) == 1


def test_low_confidence_cautious_prose_can_pass_review(facts):
    generated = json.loads(response())
    generated["events"][-1]["text"] = "辨識結果看來，球員 a 可能以殺球回擊。"
    result, _, _ = run_with_reviewer(facts, generated=json.dumps(generated, ensure_ascii=False))
    assert "可能" in result.commentary.events[-1].text


def test_accepted_tactical_wording_reaches_only_overlapping_review_context(facts):
    _, compact = facts
    observation = accepted_observation(compact)
    generated = json.loads(response())
    generated["events"][1]["text"] = observation.observation
    result, _, reviewer = run_with_reviewer(
        facts, observations=[observation], generated=json.dumps(generated, ensure_ascii=False))
    payload = json.loads(reviewer.calls[0].user_prompt)
    by_index = {item["event_index"]: item for item in payload["events"]}
    assert by_index[104]["overlapping_tactical_observations"][0]["fact_id"] == observation.fact_id
    assert by_index[97]["overlapping_tactical_observations"][0]["observation"] == observation.observation
    assert by_index[205]["overlapping_tactical_observations"] == []
    assert result.commentary.events[1].text == observation.observation


@pytest.mark.parametrize(("text", "verdict", "code"), [
    ("球員 b 打出小球。", "reject", "unsupported_identity"),
    ("球員 a 打出殺球。", "reject", "text_evidence_mismatch"),
    ("球員 b 先打高遠球，球員 a 才打小球。", "reject", "text_evidence_mismatch"),
    ("球員 a 來到網前打出小球。", "reject", "unsupported_movement_claim"),
    ("球員 a 在後場殺球。", "reject", "unsupported_spatial_claim"),
    ("球員 a 刻意以小球讓對手放棄回擊。", "reject", "unsupported_intent_or_opportunity"),
    ("這拍直接造成對手受傷。", "reject", "unsupported_causality"),
    ("球員 a 以殺球贏得這一分。", "reject", "unsupported_outcome_or_score"),
    ("球員 a 以正手小球回擊。", "reject", "unsupported_stroke_side"),
    ("球員 a 的球速達到三百公里。", "reject", "unsupported_motion_or_physics"),
    ("球員 a 從後場一路跑到網前。", "reject", "unsupported_movement_claim"),
    ("球員 a 看準對手空檔出手。", "uncertain", "unsupported_intent_or_opportunity"),
])
def test_nonpass_event_semantic_verdict_uses_safe_fallback(facts, text, verdict, code):
    generated = json.loads(response())
    generated["events"][0]["text"] = text
    reviewed = reviewer_response(verdicts=[
        (104, verdict, [code]), (97, "pass", []), (205, "pass", []),
    ])
    result, provider, reviewer = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False), reviewed=reviewed)
    assert result.commentary.events[0].text == "球員 a 以小球回擊。"
    assert text not in result.commentary.events[0].text
    diagnostic = result.event_output_diagnostics[0]
    assert diagnostic.reviewer_verdict == verdict
    assert diagnostic.violation_codes == [code]
    assert diagnostic.deterministic_gate == "not_evaluated"
    assert diagnostic.deterministic_violation_code is None
    assert diagnostic.text_source == "deterministic_fallback"
    assert result.commentary.events[1].text == generated["events"][1]["text"]
    assert result.event_output_diagnostics[1].text_source == "generated"
    assert len(provider.calls) == len(reviewer.calls) == 1


def test_uncertain_broadcast_interpretation_uses_safe_fallback(facts):
    generated = json.loads(response())
    generated["events"][0]["text"] = "球員 a 順勢變換節奏，抓準機會打出小球。"
    reviewed = reviewer_response(verdicts=[
        (104, "uncertain", ["broadcast_interpretation"]),
        (97, "pass", []), (205, "pass", []),
    ])
    result, _, _ = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False), reviewed=reviewed)
    assert result.commentary.events[0].text == "球員 a 以小球回擊。"
    assert result.event_review_verdicts[0].verdict == "uncertain"
    assert result.event_output_diagnostics[0].text_source == "deterministic_fallback"


@pytest.mark.parametrize(("text", "violation_code"), [
    ("球員 a 一路跑向前場後打出小球。", "unsupported_motion_or_physics"),
    ("球員 a 以小球贏得這一分。", "unsupported_outcome_or_score"),
])
def test_reviewer_pass_but_deterministic_violation_falls_back_per_event(
    facts, text, violation_code,
):
    generated = json.loads(response())
    generated["events"][0]["text"] = text
    result, provider, reviewer = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False))

    assert [event.stroke_index for event in result.commentary.events] == [104, 97, 205]
    assert result.commentary.events[0].text == "球員 a 以小球回擊。"
    assert result.commentary.events[1].text == generated["events"][1]["text"]
    diagnostic = result.event_output_diagnostics[0]
    assert diagnostic.reviewer_verdict == "pass"
    assert diagnostic.violation_codes == []
    assert diagnostic.deterministic_gate == "failed"
    assert diagnostic.deterministic_violation_code == violation_code
    assert diagnostic.text_source == "deterministic_fallback"
    assert result.event_output_diagnostics[1].deterministic_gate == "passed"
    assert result.event_output_diagnostics[1].text_source == "generated"
    assert result.commentary.summary is not None
    assert len(provider.calls) == len(reviewer.calls) == 1


def test_confirmed_reliable_court_fact_is_available_to_reviewer(facts):
    from modules.commentary.facts.schemas import CompactCourtPositionFact

    rally, compact = facts
    compact.events[0].court_position = CompactCourtPositionFact(
        fact_id="rally:7:stroke:104:court", source_frame=30, quality="reliable",
        position_source="ankles_midpoint", court_x_m=3.0, court_y_m=3.0,
        normalized_x=.5, normalized_y=.2, depth_zone="rear", width_zone="center",
        displacement_from_previous_hit_m=None, limitations=["sampled_hit_position_only"],
    )
    generated = json.loads(response())
    generated["events"][0]["text"] = "球員 a 在後場打出小球。"
    result, _, reviewer = run_with_reviewer(
        (rally, compact), generated=json.dumps(generated, ensure_ascii=False))
    payload = json.loads(reviewer.calls[0].user_prompt)
    assert payload["events"][0]["trusted_court_facts_available"] is True
    assert payload["events"][0]["canonical_event"]["trusted_court_fact"]["depth_zone"] == "rear"
    assert result.commentary.events[0].text.endswith("小球。")


def test_no_court_fact_exposes_unavailable_capability(facts):
    reviewed = reviewer_response(verdicts=[
        (104, "reject", ["unsupported_spatial_claim"]),
        (97, "pass", []), (205, "pass", []),
    ])
    generated = json.loads(response())
    generated["events"][0]["text"] = "球員 a 在網前打出小球。"
    result, _, _ = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False), reviewed=reviewed)
    assert result.commentary.events[0].text == "球員 a 以小球回擊。"
    assert not any(word in result.commentary.events[0].text for word in ("前場", "中場", "後場"))


def test_rejected_event_fallback_uses_trusted_court_zone(facts):
    from modules.commentary.facts.schemas import CompactCourtPositionFact

    rally, compact = facts
    compact.events[0].court_position = CompactCourtPositionFact(
        fact_id="rally:7:stroke:104:court", source_frame=30, quality="reliable",
        position_source="ankles_midpoint", court_x_m=3.0, court_y_m=6.0,
        normalized_x=.5, normalized_y=.45, depth_zone="mid", width_zone="center",
        displacement_from_previous_hit_m=None, limitations=["sampled_hit_position_only"],
    )
    generated = json.loads(response())
    generated["events"][0]["text"] = "球員 a 在後場明確擊出放小球。"
    reviewed = reviewer_response(verdicts=[
        (104, "reject", ["unsupported_spatial_claim"]),
        (97, "pass", []), (205, "pass", []),
    ])
    result, _, _ = run_with_reviewer(
        (rally, compact), generated=json.dumps(generated, ensure_ascii=False), reviewed=reviewed)
    assert result.commentary.events[0].text == "球員 a 於中場中央以小球回擊。"
    assert "後場" not in result.commentary.events[0].text
    assert "放小球" not in result.commentary.events[0].text


def test_multiple_nonpass_events_each_receive_deterministic_fallback(facts):
    generated = json.loads(response())
    generated["events"][0]["text"] = "REJECTED ONE"
    generated["events"][1]["text"] = "REJECTED TWO"
    reviewed = reviewer_response(verdicts=[
        (104, "reject", ["text_evidence_mismatch"]),
        (97, "uncertain", ["broadcast_interpretation"]),
        (205, "pass", []),
    ])
    result, provider, reviewer = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False), reviewed=reviewed)
    assert [event.text for event in result.commentary.events[:2]] == [
        "球員 a 以小球回擊。", "球員 b 以高遠球回擊。",
    ]
    assert [item.text_source for item in result.event_output_diagnostics] == [
        "deterministic_fallback", "deterministic_fallback", "generated",
    ]
    assert all("REJECTED" not in event.text for event in result.commentary.events)
    assert len(provider.calls) == len(reviewer.calls) == 1


@pytest.mark.parametrize("confidence", [.3, .6])
def test_low_or_cautious_rejected_event_fallback_is_cautious(facts, confidence):
    facts[0].events[-1].stroke_confidence = confidence
    facts[1].events[-1].stroke_confidence = confidence
    reviewed = reviewer_response(verdicts=[
        (104, "pass", []), (97, "pass", []),
        (205, "reject", ["text_evidence_mismatch"]),
    ])
    result, _, _ = run_with_reviewer(facts, reviewed=reviewed)
    assert result.commentary.events[-1].text == "球員 a 這拍可能以殺球回擊。"
    assert result.event_output_diagnostics[-1].text_source == "deterministic_fallback"


@pytest.mark.parametrize("reviewed", [
    "{",
    reviewer_response(verdicts=[(104, "pass", []), (97, "pass", [])]),
    reviewer_response(verdicts=[
        (104, "pass", []), (104, "pass", []), (97, "pass", []), (205, "pass", []),
    ]),
    reviewer_response(verdicts=[
        (104, "pass", []), (97, "pass", []), (205, "pass", []), (999, "pass", []),
    ]),
    reviewer_response(summary=None),
    json.dumps(dict(
        events=[
            dict(event_index=104, verdict="pass", violation_codes=[], explanation="extra"),
            dict(event_index=97, verdict="pass", violation_codes=[]),
            dict(event_index=205, verdict="pass", violation_codes=[]),
        ], summary=dict(verdict="pass", violation_codes=[]),
    )),
])
def test_reviewer_contract_failure_fails_closed(facts, reviewed):
    provider = FakeProvider(response())
    reviewer = FakeProvider(reviewed)
    with pytest.raises(CommentarySemanticReviewError):
        CommentaryService(provider=provider, reviewer=reviewer).generate(
            rally_fact=facts[0], compact_facts=facts[1],
            court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
        )
    assert len(provider.calls) == len(reviewer.calls) == 1


def test_incomplete_reviewer_response_fails_closed(facts):
    diagnostic = ProviderDiagnostics(
        finish_reason="MAX_TOKENS", requested_model="review-test", candidate_count=1,
        text_present=True, parsed_content_present=False,
    )
    error = ProviderError("incomplete_response", "incomplete", diagnostics=diagnostic)
    provider = FakeProvider(response())
    reviewer = FakeProvider("", error=error)
    with pytest.raises(ProviderError) as caught:
        CommentaryService(provider=provider, reviewer=reviewer).generate(
            rally_fact=facts[0], compact_facts=facts[1],
            court_position_to_player=CourtPositionToPlayer(top="a", bottom="b"),
        )
    assert caught.value is error
    assert len(provider.calls) == len(reviewer.calls) == 1


@pytest.mark.parametrize(("verdict", "code"), [
    ("reject", "unsupported_spatial_claim"),
    ("uncertain", "text_evidence_mismatch"),
])
def test_nonpass_summary_is_omitted_with_diagnostics(facts, verdict, code):
    result, provider, reviewer = run_with_reviewer(
        facts, reviewed=reviewer_response(summary=(verdict, [code])))
    assert result.commentary.summary is None
    assert result.summary_omitted_by_review
    assert result.summary_review_verdict.verdict == verdict
    assert result.summary_review_verdict.violation_codes == [code]
    assert len(provider.calls) == len(reviewer.calls) == 1


def test_unsupported_spatial_movement_summary_is_omitted(facts):
    generated = json.loads(response())
    generated["summary"] = "球員 a 從後場一路移動到網前並迫使對手失誤。"
    result, _, _ = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False),
        reviewed=reviewer_response(summary=("reject", [
            "unsupported_movement_claim", "unsupported_spatial_claim",
        ])),
    )
    assert result.commentary.summary is None
    assert result.summary_omitted_by_review


def test_advisory_summary_is_retained_with_diagnostics(facts):
    generated = json.loads(response())
    generated["summary"] = "雙方調動節奏，球員 a 順勢展開進攻。"
    result, _, _ = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False),
        reviewed=reviewer_response(summary=("uncertain", ["broadcast_interpretation"])),
    )
    assert result.commentary.summary is None
    assert result.summary_omitted_by_review
    assert result.summary_review_verdict.verdict == "uncertain"


@pytest.mark.parametrize("event_index", [True, "104", 1.5])
def test_reviewer_event_index_is_strict(event_index):
    with pytest.raises(ValidationError):
        CommentaryReviewResponse.model_validate(dict(
            events=[dict(event_index=event_index, verdict="pass", violation_codes=[])],
            summary=None,
        ))


def test_safe_summary_passes_batch_review(facts):
    result, _, _ = run_with_reviewer(facts)
    assert result.commentary.summary.text == "這段來回呈現多種擊球的接續。"
    assert result.summary_review_verdict.verdict == "pass"


def test_stroke_taxonomy_prompt_separates_shot_name_from_player_position():
    prompt = REVIEW_PROMPT_PATH.read_text(encoding="utf-8")
    for phrase in ["canonical 小球", "放小球", "acceptable natural lexicalizations",
                   "在網前", "requires trusted court evidence", "高遠球 merges",
                   "平快球 merges", "never reconstruct the hidden fine class"]:
        assert phrase in prompt


def test_hidden_fine_stroke_labels_derive_from_authoritative_merge():
    assert _HIDDEN_FINE_STROKE_LABELS == {
        "放小球", "擋小球", "挑球", "長球", "平球", "推球", "發短球", "發長球",
    }


@pytest.mark.parametrize("fine_label", sorted({
    "放小球", "擋小球", "挑球", "長球", "平球", "推球", "發短球", "發長球",
}))
def test_deterministic_gate_rejects_complete_hidden_fine_label(fine_label):
    with pytest.raises(CommentaryGenerationError, match="hidden fine stroke class"):
        _validate_text(f"球員 a 明確擊出{fine_label}。", cautious=False, label="event 1")


@pytest.mark.parametrize("text", [
    "球員 a 擊出小球。",
    "球員 a 回以小球。",
    "球員 a 以小球處理。",
    "球員 a 拉出高遠球。",
    "球員 a 推進節奏後擊出小球。",
])
def test_merged_label_and_ordinary_style_verbs_remain_valid(text):
    _validate_text(text, cautious=False, label="event 1")


@pytest.mark.parametrize("lexicalization", ["放小球", "擋小球"])
def test_canonical_small_shot_lexicalization_is_retained(facts, lexicalization):
    generated = json.loads(response())
    generated["events"][0]["text"] = f"球員 a 擊出{lexicalization}。"
    result, _, _ = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False))
    assert result.commentary.events[0].text == generated["events"][0]["text"]
    assert result.event_output_diagnostics[0].text_source == "generated"


def test_summary_can_lexicalize_supplied_canonical_small_shot(facts):
    generated = json.loads(response())
    generated["summary"] = "這段來回包含多次放小球的接續。"
    result, _, _ = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False))
    assert result.commentary.summary.text == generated["summary"]


def test_commentator_prompt_preserves_merged_stroke_granularity():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    for phrase in ["merged stroke-label granularity", "permitted natural commentary lexicalizations",
                   "放小球", "擋小球", "挑球 versus 長球",
                   "平球 versus 推球", "發短球 versus 發長球"]:
        assert phrase in prompt


@pytest.mark.parametrize("text", [
    "球員 a 明確擊出放小球。",
    "球員 a 這拍是擋小球。",
    "球員 a 選擇挑球。",
    "球員 a 擊出長球。",
    "球員 a 擊出平球。",
    "球員 a 以推球回應。",
    "球員 a 發出發短球。",
    "球員 a 發出發長球。",
])
def test_deterministic_gate_rejects_reconstructed_hidden_fine_class(text):
    with pytest.raises(CommentaryGenerationError, match="hidden fine stroke class"):
        _validate_text(text, cautious=False, label="event")


@pytest.mark.parametrize("lexicalization", ["放小球", "擋小球"])
def test_deterministic_gate_allows_small_family_lexicalization(lexicalization):
    _validate_text(
        f"球員 a 擊出{lexicalization}。", cautious=False, label="event",
        canonical_stroke_type="小球",
    )


@pytest.mark.parametrize("fine_service", ["發短球", "發長球"])
def test_deterministic_gate_rejects_unsupported_service_variant(fine_service):
    with pytest.raises(CommentaryGenerationError, match="hidden fine stroke class"):
        _validate_text(
            f"球員 a 擊出{fine_service}。", cautious=False, label="event",
            canonical_stroke_type="發球",
        )


@pytest.mark.parametrize("text", [
    "球員 a 擊出小球。",
    "球員 a 回以小球。",
    "球員 a 以小球處理。",
    "球員 a 拉出高遠球。",
    "球員 a 向前推進後擊出小球。",
])
def test_deterministic_gate_allows_merged_labels_and_ordinary_verbs(text):
    _validate_text(text, cautious=False, label="event")


@pytest.mark.parametrize("fine_service", ["發短球", "發長球"])
def test_reviewer_service_variant_violation_uses_canonical_fallback(facts, fine_service):
    facts[0].events[0].stroke_type = "發球"
    facts[1].events[0].stroke_type = "發球"
    generated = json.loads(response())
    generated["events"][0]["text"] = f"球員 a 擊出{fine_service}。"
    reviewed = reviewer_response(verdicts=[
        (104, "reject", ["unsupported_fine_stroke_class"]),
        (97, "pass", []), (205, "pass", []),
    ])
    result, _, _ = run_with_reviewer(
        facts, generated=json.dumps(generated, ensure_ascii=False), reviewed=reviewed)
    assert result.commentary.events[0].text == "球員 a 以發球回擊。"
    assert result.event_output_diagnostics[0].violation_codes == [
        "unsupported_fine_stroke_class"
    ]


def test_reviewer_prompt_states_merged_taxonomy_boundary():
    prompt = REVIEW_PROMPT_PATH.read_text(encoding="utf-8")
    assert "unsupported_fine_stroke_class" in prompt
    assert "拉出高遠球 remain valid" in prompt
    generator_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "Preserve the canonical merged stroke-label granularity" in generator_prompt
