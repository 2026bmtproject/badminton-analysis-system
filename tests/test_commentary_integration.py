"""Offline production-stage integration; no network or Gemini calls."""

import json
import subprocess
import sys
import pytest
from pydantic import TypeAdapter, ValidationError

from modules.artifacts import read_artifact, write_artifact
from modules.base import StageState, StageStatus, write_status
from modules.commentary import CommentaryModule
from modules.commentary.module import (
    ARTIFACT_VERSION,
    CommentaryModuleConfig,
    CommentaryProviders,
    FAILURE_DIAGNOSTIC_NAME,
    identity_mapping_for_segment,
    read_identity_epochs,
)
from modules.commentary.providers.base import (
    ProviderDiagnostics,
    ProviderError,
    ProviderResponse,
    TokenUsage,
)
from modules.commentary.providers.fake import FakeProvider
from modules.contracts import (
    PIPELINE,
    CommentaryRally,
    CourtCalibration,
    HighlightScore,
    HitEvent,
    PlayerIdentityEpoch,
    PoseFrame,
    RallyScore,
    Segment,
    StrokeLabel,
    artifact_path,
    stage_path,
)
from modules.runner import available_modules, stale_inputs


class TacticalPass(FakeProvider):
    def generate(self, **kwargs):
        candidates = json.loads(kwargs["user_prompt"])["candidates"]
        self.response = json.dumps({"verdicts": [
            {"candidate_id": item["candidate_id"], "verdict": "pass", "violation_codes": []}
            for item in candidates
        ]})
        return super().generate(**kwargs)


class CommentaryPass(FakeProvider):
    def generate(self, **kwargs):
        payload = json.loads(kwargs["user_prompt"])
        self.response = json.dumps({
            "events": [
                {"event_index": item["event_index"], "verdict": "pass", "violation_codes": []}
                for item in payload["events"]
            ],
            "summary": ({"verdict": "pass", "violation_codes": []}
                        if payload["summary"] is not None else None),
        })
        return super().generate(**kwargs)


class CommentarySummaryReject(CommentaryPass):
    def generate(self, **kwargs):
        response = super().generate(**kwargs)
        parsed = json.loads(response.text)
        parsed["summary"] = {"verdict": "reject", "violation_codes": ["text_evidence_mismatch"]}
        return ProviderResponse(json.dumps(parsed), response.model, response.usage)


class CommentaryEventReject(CommentaryPass):
    def generate(self, **kwargs):
        response = super().generate(**kwargs)
        parsed = json.loads(response.text)
        parsed["events"][0] = {
            "event_index": parsed["events"][0]["event_index"],
            "verdict": "reject",
            "violation_codes": ["text_evidence_mismatch"],
        }
        return ProviderResponse(json.dumps(parsed), response.model, response.usage)


def _commentator_response(summary="雙方完成一段多拍來回。"):
    return json.dumps({
        "events": [
            {"event_index": 0, "text": "球員 a 以小球回擊。"},
            {"event_index": 1, "text": "球員 b 接著打出高遠球。"},
            {"event_index": 2, "text": "球員 a 隨後以殺球回擊。"},
        ],
        "summary": summary,
    }, ensure_ascii=False)


def _providers(*, tactical=None, tactical_reviewer=None,
               commentator=None, commentary_reviewer=None):
    return CommentaryProviders(
        tactical_generator=tactical or FakeProvider('{"observations":[]}'),
        tactical_reviewer=tactical_reviewer or TacticalPass(""),
        commentator=commentator or FakeProvider(_commentator_response()),
        commentary_reviewer=commentary_reviewer or CommentaryPass(""),
    )


def _write_stage(match, stage, records, extra=None, *, completed=True):
    write_artifact(PIPELINE[stage], records, artifact_path(match, stage), extra)
    if completed:
        write_status(stage_path(match, stage), StageState(name=stage, status=StageStatus.COMPLETED))


def _match(tmp_path, *, identity=True, reverse=False, sub_scores=None, highlight=None):
    _write_stage(tmp_path, "match_segmentation", [
        Segment(0, 29, 0, .967, .967), Segment(30, 90, 1, 3, 2),
    ], {"fps": 30})
    _write_stage(tmp_path, "event_detection", [HitEvent(30), HitEvent(45), HitEvent(60)])
    _write_stage(tmp_path, "stroke_classification", [
        StrokeLabel(2, 60, 1, "top", "殺球", .9),
        StrokeLabel(0, 30, 1, "top", "小球", .9),
        StrokeLabel(1, 45, 1, "bottom", "高遠球", .9),
    ], {"shuttle_method": "inpaint"})
    score = RallyScore(1, 3, 4)
    score.sub_scores = sub_scores
    _write_stage(tmp_path, "score_recognition", [score])
    epochs = []
    if identity:
        epochs = [PlayerIdentityEpoch(
            epoch_index=0, game_index=1, first_segment=1, last_segment=1,
            top="b" if reverse else "a", bottom="a" if reverse else "b",
            votes=4, agreement=.75, resolved_by="vote",
        )]
    _write_stage(tmp_path, "player_identity", epochs, {"unresolved": []})
    if highlight is not None:
        _write_stage(tmp_path, "highlight_ranking", [HighlightScore(1, highlight)])
    return tmp_path


def _run(match, providers=None):
    module = CommentaryModule(
        config=CommentaryModuleConfig(model="offline"),
        providers=providers or _providers(),
    )
    module.run(match)
    return module, read_artifact(PIPELINE["commentary"], module.get_output_path(match))


def test_runner_registration_dependencies_order_and_sdk_neutral_import():
    modules = available_modules()
    assert list(modules).count("commentary") == 1
    module = modules["commentary"]
    assert module.dependencies == [
        "match_segmentation", "event_detection", "stroke_classification",
        "score_recognition", "player_identity",
    ]
    assert module.optional_dependencies == [
        "highlight_ranking", "pose", "court_detection", "shuttle_tracking",
    ]
    assert list(modules).index("player_identity") < list(modules).index("commentary")
    script = """
import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'google.genai' or name.startswith('google.genai.'):
        raise AssertionError('runner imported Gemini SDK eagerly')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import modules.runner
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_readiness_requires_completed_hard_artifacts(tmp_path):
    match = _match(tmp_path)
    module = CommentaryModule(config=CommentaryModuleConfig(), providers=_providers())
    assert module.check_ready(match)
    artifact_path(match, "player_identity").unlink()
    assert not module.check_ready(match)


def test_only_semantic_reviewers_use_low_thinking(monkeypatch):
    import modules.commentary.providers.gemini as gemini_module

    configs = []

    class RecordingProvider:
        def __init__(self, config):
            self.config = config
            configs.append(config)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(gemini_module, "GeminiProvider", RecordingProvider)
    module = CommentaryModule(config=CommentaryModuleConfig())
    with module._provider_bundle(module.config) as providers:
        assert providers.commentary_reviewer.config.thinking_level == "low"
    assert [config.thinking_level for config in configs] == [None, "low", None, "low"]
    assert [config.max_output_tokens for config in configs] == [4096, 4096, 4096, 4096]


def test_identity_normal_reverse_unavailable_and_ambiguous(tmp_path):
    match = _match(tmp_path)
    epoch, mapping = identity_mapping_for_segment(read_identity_epochs(match), 1)
    assert (mapping.top, mapping.bottom, epoch.resolved_by) == ("a", "b", "vote")
    reversed_epoch = PlayerIdentityEpoch(1, 1, 2, 3, "b", "a", 0, 0, "convention")
    assert identity_mapping_for_segment([reversed_epoch], 2)[1].top == "b"
    assert identity_mapping_for_segment([reversed_epoch], 1) is None
    with pytest.raises(ValueError, match="multiple epochs"):
        identity_mapping_for_segment([reversed_epoch, reversed_epoch], 2)


@pytest.mark.parametrize("source", ["hsv_fallback", "hsv_default"])
def test_identity_reader_accepts_visual_fallback_provenance(tmp_path, source):
    match = _match(tmp_path)
    path = artifact_path(match, "player_identity")
    envelope = read_artifact(PIPELINE["player_identity"], path)
    envelope["epochs"][0]["resolved_by"] = source
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert read_identity_epochs(match)[0].resolved_by == source


@pytest.mark.parametrize("bad", [
    {"top": "top"}, {"bottom": "a"}, {"agreement": float("nan")},
    {"first_segment": 2, "last_segment": 1}, {"resolved_by": "guess"},
])
def test_identity_artifact_is_strict(tmp_path, bad):
    match = _match(tmp_path)
    path = artifact_path(match, "player_identity")
    row = read_artifact(PIPELINE["player_identity"], path)["epochs"][0] | bad
    path.write_text(json.dumps({"epochs": [row]}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid player_identity"):
        read_identity_epochs(match)


def test_success_persists_canonical_artifact_and_diagnostics(tmp_path):
    match = _match(tmp_path, highlight=0.0)
    providers = _providers()
    _, artifact = _run(match, providers)
    assert artifact["schema_version"] == ARTIFACT_VERSION
    assert artifact["runtime"]["model"] == "offline"
    assert len(artifact["rallies"]) == 1
    restored = TypeAdapter(CommentaryRally).validate_python(artifact["rallies"][0])
    assert [event.stroke_index for event in restored.events] == [0, 1, 2]
    assert [event.frame for event in restored.events] == [30, 45, 60]
    assert [event.time_sec for event in restored.events] == [1.0, 1.5, 2.0]
    assert [event.player for event in restored.events] == ["a", "b", "a"]
    assert restored.summary is not None
    assert artifact["diagnostics"][0]["tactical"]["outcome"] == "generated_empty"
    plan = json.loads(providers.commentator.calls[0].user_prompt)
    assert plan["highlight_context"]["ranking_score"] == 0.0
    assert len(providers.tactical_generator.calls) == 1
    assert len(providers.tactical_reviewer.calls) == 0
    assert len(providers.commentator.calls) == len(providers.commentary_reviewer.calls) == 1


def test_reversed_mapping_is_canonical_and_summary_none_supported(tmp_path):
    match = _match(tmp_path, reverse=True)
    providers = _providers(commentary_reviewer=CommentarySummaryReject(""))
    # Reversed mapping changes canonical authors in generated text too.
    payload = json.loads(providers.commentator.response)
    payload["events"][0]["text"] = "球員 b 以小球回擊。"
    payload["events"][1]["text"] = "球員 a 接著打出高遠球。"
    payload["events"][2]["text"] = "球員 b 隨後以殺球回擊。"
    providers.commentator.response = json.dumps(payload, ensure_ascii=False)
    _, artifact = _run(match, providers)
    rally = TypeAdapter(CommentaryRally).validate_python(artifact["rallies"][0])
    assert [event.player for event in rally.events] == ["b", "a", "b"]
    assert rally.summary is None


def test_reviewer_rejection_persists_deterministic_fallback(tmp_path):
    match = _match(tmp_path)
    generated = json.loads(_commentator_response())
    generated["events"][0]["text"] = "球員 a 贏得這一分。"
    providers = _providers(
        commentator=FakeProvider(json.dumps(generated, ensure_ascii=False)),
        commentary_reviewer=CommentaryEventReject(""),
    )
    _, artifact = _run(match, providers)
    rally = TypeAdapter(CommentaryRally).validate_python(artifact["rallies"][0])
    assert rally.events[0].text == "球員 a 以小球回擊。"
    diagnostic = artifact["diagnostics"][0]["commentary"]["event_output_diagnostics"][0]
    assert diagnostic == {
        "event_index": 0,
        "reviewer_verdict": "reject",
        "violation_codes": ["text_evidence_mismatch"],
        "deterministic_gate": "not_evaluated",
        "deterministic_violation_code": None,
        "text_source": "deterministic_fallback",
    }


def test_unavailable_identity_and_multi_rally_are_explicit(tmp_path):
    match = _match(tmp_path, identity=False)
    # No production provider is constructed when every segment is unsupported.
    module = CommentaryModule(config=CommentaryModuleConfig(model="offline"))
    module.run(match)
    artifact = read_artifact(PIPELINE["commentary"], module.get_output_path(match))
    assert artifact["rallies"] == []
    assert artifact["unsupported_segments"] == [
        {"segment_index": index, "reason": "player_identity_unavailable"}
        for index in (0, 1)
    ]
    match = _match(tmp_path / "multi", sub_scores=[[2, 4], [3, 4]])
    _, artifact = _run(match, _providers())
    assert artifact["rallies"] == []
    assert {item["reason"] for item in artifact["unsupported_segments"]} == {
        "player_identity_unavailable", "multiple_recovered_rallies_unsupported",
    }


def test_tactical_observation_is_reviewed_and_supplied(tmp_path):
    match = _match(tmp_path)
    proposal = {"observation": "球員 a 的小球後接球員 b 的高遠球。", "supporting_claims": [
        {"fact_id": "rally:1:stroke:0", "field": "stroke_type"},
        {"fact_id": "rally:1:stroke:1", "field": "stroke_type"},
    ]}
    providers = _providers(tactical=FakeProvider(json.dumps({"observations": [proposal]}, ensure_ascii=False)))
    _, artifact = _run(match, providers)
    tactical = artifact["diagnostics"][0]["tactical"]
    assert tactical["outcome"] == "accepted" and len(tactical["observations"]) == 1
    assert artifact["diagnostics"][0]["commentary"]["supplied_tactical_observation_ids"] == [
        tactical["observations"][0]["fact_id"]
    ]
    assert len(providers.tactical_reviewer.calls) == 1


@pytest.mark.parametrize("failure", [
    ProviderError("request_failed", "offline failure"),
    None,
])
def test_provider_or_structured_failure_publishes_no_partial_artifact(tmp_path, failure):
    match = _match(tmp_path)
    commentator = (FakeProvider("", error=failure) if failure
                   else FakeProvider('{"events":[]}'))
    module = CommentaryModule(
        config=CommentaryModuleConfig(model="offline"),
        providers=_providers(commentator=commentator),
    )
    with pytest.raises((ProviderError, ValueError)):
        module.run(match)
    assert not module.get_output_path(match).exists()
    assert not module.get_output_path(match).with_suffix(".json.tmp").exists()


def test_tactical_provider_failure_is_fail_closed(tmp_path):
    match = _match(tmp_path)
    error = ProviderError("request_failed", "offline tactical failure")
    providers = _providers(tactical=FakeProvider("", error=error))
    module = CommentaryModule(
        config=CommentaryModuleConfig(model="offline"), providers=providers,
    )
    with pytest.raises(ProviderError) as caught:
        module.run(match)
    assert caught.value is error
    assert len(providers.commentator.calls) == 0
    assert not module.get_output_path(match).exists()


def _grounded_tactical_response():
    return json.dumps({"observations": [{
        "observation": "球員 a 的小球後接球員 b 的高遠球。",
        "supporting_claims": [
            {"fact_id": "rally:1:stroke:0", "field": "stroke_type"},
            {"fact_id": "rally:1:stroke:1", "field": "stroke_type"},
        ],
    }]}, ensure_ascii=False)


@pytest.mark.parametrize("phase", [
    "tactical_generation", "tactical_review",
    "commentary_generation", "commentary_review",
])
def test_provider_failure_persists_safe_phase_diagnostic_without_retry(tmp_path, phase):
    match = _match(tmp_path)
    diagnostics = ProviderDiagnostics(
        finish_reason="MAX_TOKENS", finish_detail="output limit",
        requested_model="offline-requested", returned_model="offline-returned",
        usage=TokenUsage(input_tokens=11, output_tokens=12, thought_tokens=13, total_tokens=36),
        candidate_count=1, text_present=True, parsed_content_present=False,
    )
    failure = ProviderError(
        "incomplete_response", "safe incomplete response", diagnostics=diagnostics,
    )
    failing = FakeProvider("", error=failure)
    providers = _providers(
        tactical=(failing if phase == "tactical_generation"
                  else FakeProvider(_grounded_tactical_response())
                  if phase == "tactical_review" else None),
        tactical_reviewer=failing if phase == "tactical_review" else None,
        commentator=failing if phase == "commentary_generation" else None,
        commentary_reviewer=failing if phase == "commentary_review" else None,
    )
    module = CommentaryModule(
        config=CommentaryModuleConfig(model="offline"), providers=providers,
    )
    with pytest.raises(ProviderError) as caught:
        module.run(match)
    assert caught.value is failure
    saved = json.loads(
        (stage_path(match, "commentary") / FAILURE_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert saved["schema_version"] == "commentary-provider-failure-v1"
    assert saved["segment_index"] == 1
    assert saved["phase"] == phase
    assert saved["error"] == {
        "code": "incomplete_response", "message": "safe incomplete response",
    }
    assert saved["provider"]["finish_reason"] == "MAX_TOKENS"
    assert saved["provider"]["requested_model"] == "offline-requested"
    assert saved["provider"]["returned_model"] == "offline-returned"
    assert saved["provider"]["usage"] == {
        "input_tokens": 11, "output_tokens": 12,
        "thought_tokens": 13, "total_tokens": 36,
    }
    assert saved["provider"]["latency_seconds"] >= 0
    assert saved["call"]["phase_index"] == 1
    assert len(failing.calls) == 1
    assert saved["completed"][phase] == 0
    assert not module.get_output_path(match).exists()
    assert json.loads((stage_path(match, "commentary") / "status.json").read_text(
        encoding="utf-8"
    ))["status"] == "failed"


def test_provider_failure_handles_missing_diagnostics_and_redacts_requests(tmp_path):
    class LeakingProvider(FakeProvider):
        def generate(self, **kwargs):
            self.error = ProviderError(
                "request_failed",
                f"{kwargs['system_prompt']} {kwargs['user_prompt']} "
                "Authorization: Bearer secret-value",
            )
            return super().generate(**kwargs)

    match = _match(tmp_path)
    leaking = LeakingProvider("")
    module = CommentaryModule(
        config=CommentaryModuleConfig(model="offline"),
        providers=_providers(tactical=leaking),
    )
    with pytest.raises(ProviderError):
        module.run(match)
    path = stage_path(match, "commentary") / FAILURE_DIAGNOSTIC_NAME
    text = path.read_text(encoding="utf-8")
    saved = json.loads(text)
    assert saved["provider"]["finish_reason"] is None
    assert saved["provider"]["usage"] is None
    assert "secret-value" not in text
    assert "source_catalog" not in text


def test_success_removes_stale_provider_failure_diagnostic(tmp_path):
    match = _match(tmp_path)
    diagnostic = stage_path(match, "commentary") / FAILURE_DIAGNOSTIC_NAME
    diagnostic.parent.mkdir(parents=True, exist_ok=True)
    diagnostic.write_text('{"stale":true}', encoding="utf-8")
    module, _ = _run(match)
    assert not diagnostic.exists()
    assert module.get_output_path(match).exists()


def test_malformed_commentary_review_publishes_no_partial_artifact(tmp_path):
    match = _match(tmp_path)
    module = CommentaryModule(
        config=CommentaryModuleConfig(model="offline"),
        providers=_providers(commentary_reviewer=FakeProvider('{"events":[]}')),
    )
    with pytest.raises(ValueError):
        module.run(match)
    assert not module.get_output_path(match).exists()


def test_optional_court_confirmation_and_geometry_policy(tmp_path):
    match = _match(tmp_path)
    keypoints = [[2 + (index % 2) * .2, 3 + index * .1, .9] for index in range(17)]
    poses = [PoseFrame(frame, 1, player, keypoints, [2, 2, 4, 6])
             for frame, player in [(30, "top"), (45, "bottom"), (60, "top")]]
    calibration = CourtCalibration([], [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 1)
    _write_stage(match, "pose", poses)
    _write_stage(match, "court_detection", [calibration], {"confirmed": False})
    _run(match)
    # Read the Commentator request: no trusted court fact was supplied.
    providers = _providers()
    _run(match, providers)
    plan = json.loads(providers.commentator.calls[0].user_prompt)
    assert all(event["court_zone"] is None for event in plan["events"])

    _write_stage(match, "court_detection", [calibration], {"confirmed": True})
    providers = _providers()
    _run(match, providers)
    plan = json.loads(providers.commentator.calls[0].user_prompt)
    assert all(event["court_zone"] is not None for event in plan["events"])

    invalid = CourtCalibration([], [[0, 0, 0], [0, 0, 0], [0, 0, 0]], 1)
    _write_stage(match, "court_detection", [invalid], {"confirmed": True})
    providers = _providers()
    _run(match, providers)
    plan = json.loads(providers.commentator.calls[0].user_prompt)
    assert all(event["court_zone"] is None for event in plan["events"])


@pytest.mark.parametrize("dependency", [
    "player_identity", "stroke_classification", "court_detection", "highlight_ranking",
])
def test_dependency_changes_make_completed_commentary_stale(tmp_path, dependency):
    match = _match(tmp_path, highlight=0.0)
    _write_stage(match, "court_detection", [], {"confirmed": False})
    module, _ = _run(match)
    assert stale_inputs(match, module) == []
    path = artifact_path(match, dependency)
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert stale_inputs(match, module) == [dependency]


def test_malformed_persisted_artifact_rejected(tmp_path):
    path = tmp_path / "commentary.json"
    path.write_text('{"rallies":[{"segment_index":1,"events":[{"player":"top"}]}]}')
    data = read_artifact(PIPELINE["commentary"], path)
    with pytest.raises(ValidationError):
        TypeAdapter(list[CommentaryRally]).validate_python(data["rallies"])
