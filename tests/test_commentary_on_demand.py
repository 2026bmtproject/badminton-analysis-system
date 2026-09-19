"""Cost-safe selected-segment Commentary orchestration."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from modules.artifacts import read_artifact, write_artifact
from modules.base import BaseModule, StageResult, StageState, StageStatus, read_status, write_status
from modules.commentary.module import (
    CommentaryModule,
    CommentaryModuleConfig,
    CommentaryProgress,
    CommentaryProviders,
    SEGMENT_ARTIFACT_VERSION,
    generate_commentary_segments,
)
from modules.commentary.providers.base import ProviderError, ProviderResponse
from modules.commentary.providers.fake import FakeProvider
from modules.contracts import (
    PIPELINE,
    HitEvent,
    PlayerIdentityEpoch,
    RallyScore,
    Segment,
    StrokeLabel,
    artifact_path,
    stage_path,
)


class DynamicCommentator(FakeProvider):
    def generate(self, **kwargs):
        plan = json.loads(kwargs["user_prompt"])
        self.response = json.dumps({
            "events": [
                {
                    "event_index": item["event_index"],
                    "text": f"球員 {item['player']} 以{item['stroke_type']}回擊。",
                }
                for item in plan["events"]
            ],
            "summary": "雙方完成一段來回。",
        }, ensure_ascii=False)
        return super().generate(**kwargs)


class DynamicCommentaryReviewer(FakeProvider):
    def generate(self, **kwargs):
        payload = json.loads(kwargs["user_prompt"])
        self.response = json.dumps({
            "events": [
                {"event_index": item["event_index"], "verdict": "pass", "violation_codes": []}
                for item in payload["events"]
            ],
            "summary": {"verdict": "pass", "violation_codes": []},
        })
        return super().generate(**kwargs)


def _providers(*, tactical=None):
    return CommentaryProviders(
        tactical_generator=tactical or FakeProvider('{"observations":[]}'),
        tactical_reviewer=FakeProvider('{"verdicts":[]}'),
        commentator=DynamicCommentator(""),
        commentary_reviewer=DynamicCommentaryReviewer(""),
    )


def _write_stage(match, name, records, extra=None):
    write_artifact(PIPELINE[name], records, artifact_path(match, name), extra)
    write_status(
        stage_path(match, name),
        StageState(name=name, status=StageStatus.COMPLETED),
    )


def _match(tmp_path: Path, *, identity_end: int = 2) -> Path:
    _write_stage(tmp_path, "match_segmentation", [
        Segment(0, 29, 0.0, 0.967, 0.967),
        Segment(30, 59, 1.0, 1.967, 0.967),
        Segment(60, 89, 2.0, 2.967, 0.967),
    ], {"fps": 30})
    _write_stage(tmp_path, "event_detection", [HitEvent(10), HitEvent(40), HitEvent(70)])
    _write_stage(tmp_path, "stroke_classification", [
        StrokeLabel(0, 10, 0, "top", "小球", .9),
        StrokeLabel(1, 40, 1, "bottom", "高遠球", .9),
        StrokeLabel(2, 70, 2, "top", "殺球", .9),
    ])
    _write_stage(tmp_path, "score_recognition", [
        RallyScore(0, 0, 0), RallyScore(1, 1, 0), RallyScore(2, 1, 1),
    ])
    _write_stage(tmp_path, "player_identity", [PlayerIdentityEpoch(
        epoch_index=0, game_index=0, first_segment=0, last_segment=identity_end,
        top="a", bottom="b", votes=5, agreement=1.0, resolved_by="vote",
    )], {"unresolved": []})
    return tmp_path


def _module(providers=None):
    return CommentaryModule(
        config=CommentaryModuleConfig(model="offline"),
        providers=providers or _providers(),
    )


def test_selected_segments_are_sorted_deduplicated_and_isolated(tmp_path, capsys):
    match = _match(tmp_path)
    providers = _providers()
    result = _module(providers).generate_segments(
        match, [2, 0, 2], progress=CommentaryProgress(full_match=False),
    )

    assert [item.segment_index for item in result.generated] == [0, 2]
    assert result.provider_calls == 6
    assert len(providers.tactical_generator.calls) == 2
    assert len(providers.tactical_reviewer.calls) == 0
    assert len(providers.commentator.calls) == 2
    assert len(providers.commentary_reviewer.calls) == 2
    assert [path.name for path in result.artifact_paths] == [
        "segment_000.json", "segment_002.json",
    ]
    assert not artifact_path(match, "commentary").exists()
    assert read_status(stage_path(match, "commentary")) is None
    output = capsys.readouterr().out
    assert "requested segments" in output and "eligible" in output
    assert "approximately 6-8" in output
    assert "segment 0 (1/2)" in output and "segment 2 (2/2)" in output
    assert "tactical review skipped" in output
    assert "Commentary" in output and "2/2" in output
    assert "$" not in output and "monetary" not in output


def test_segment_artifact_is_self_describing_and_reuses_rally_contract(tmp_path):
    match = _match(tmp_path)
    result = _module().generate_segments(match, 1)
    payload = json.loads(result.artifact_paths[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == SEGMENT_ARTIFACT_VERSION
    assert payload["segment_index"] == payload["rally"]["segment_index"] == 1
    assert payload["rally"]["events"][0]["stroke_index"] == 1
    assert set(payload) == {
        "schema_version", "segment_index", "rally", "identity",
        "tactical", "commentary", "runtime",
    }


def test_public_api_makes_single_segment_natural_and_progress_reaches_one_of_one(
    tmp_path, capsys,
):
    match = _match(tmp_path)
    result = generate_commentary_segments(
        match,
        1,
        config=CommentaryModuleConfig(model="offline"),
        providers=_providers(),
        progress=CommentaryProgress(full_match=False),
    )
    assert [rally.segment_index for rally in result.rallies] == [1]
    output = capsys.readouterr().out
    assert "1/1" in output
    assert "tactical generation" in output
    assert "commentary generation" in output
    assert "commentary review" in output


def test_invalid_segment_fails_before_provider_calls_or_artifacts(tmp_path):
    match = _match(tmp_path)
    providers = _providers()
    with pytest.raises(ValueError, match="does not exist"):
        _module(providers).generate_segments(match, 99)
    assert all(not provider.calls for provider in providers.__dict__.values())
    assert not stage_path(match, "commentary").exists()


def test_unsupported_segment_consumes_zero_calls_and_no_fake_progress(tmp_path, capsys):
    match = _match(tmp_path, identity_end=0)
    providers = _providers()
    result = _module(providers).generate_segments(
        match, 1, progress=CommentaryProgress(full_match=False),
    )
    assert result.generated == [] and result.provider_calls == 0
    assert result.unsupported_segments == [
        {"segment_index": 1, "reason": "player_identity_unavailable"}
    ]
    assert all(not provider.calls for provider in providers.__dict__.values())
    output = capsys.readouterr().out
    assert "eligible" in output and "approximately 0-0" in output
    assert "segment 1 (1/" not in output


def test_warning_happens_before_provider_bundle_is_opened(tmp_path):
    match = _match(tmp_path)
    order = []

    class RecordingProgress:
        def preparing(self, *_): pass
        def request_notice(self, **_): order.append("warning")
        def phase(self, *_): pass
        def skipped(self, *_): pass
        def completed(self, *_): pass
        def failed(self, *_): pass
        def summary(self, **_): pass

    class RecordingModule(CommentaryModule):
        @contextmanager
        def _provider_bundle(self, config):
            order.append("provider_open")
            yield _providers()

    RecordingModule(config=CommentaryModuleConfig(model="offline")).generate_segments(
        match, 0, progress=RecordingProgress(),
    )
    assert order[:2] == ["warning", "provider_open"]


def test_failed_regeneration_keeps_success_and_writes_segment_diagnostic(tmp_path):
    match = _match(tmp_path)
    success = _module().generate_segments(match, 0).artifact_paths[0]
    previous = success.read_bytes()
    failing = FakeProvider("", error=ProviderError("request_failed", "offline"))
    providers = _providers(tactical=failing)

    with pytest.raises(ProviderError):
        _module(providers).generate_segments(match, 0)

    assert success.read_bytes() == previous
    diagnostic = success.with_suffix(".failure.json")
    assert diagnostic.is_file()
    assert json.loads(diagnostic.read_text(encoding="utf-8"))["segment_index"] == 0
    assert not (stage_path(match, "commentary") / "failure_diagnostic.json").exists()
    assert len(failing.calls) == 1
    assert not success.with_suffix(success.suffix + ".tmp").exists()


def test_provider_failure_with_write_disabled_stays_segment_scoped(tmp_path, capsys):
    match = _match(tmp_path)
    failing = FakeProvider("", error=ProviderError("request_failed", "offline"))
    with pytest.raises(ProviderError):
        _module(_providers(tactical=failing)).generate_segments(
            match, 2, write=False, progress=CommentaryProgress(full_match=False),
        )
    diagnostic = (
        stage_path(match, "commentary") / "segments" / "segment_002.failure.json"
    )
    assert diagnostic.is_file()
    assert not (stage_path(match, "commentary") / "failure_diagnostic.json").exists()
    output = capsys.readouterr()
    assert "segment 2 during tactical_generation" in output.err
    assert str(diagnostic) in output.err


def test_full_match_path_still_owns_canonical_artifact_and_status(tmp_path, capsys):
    match = _match(tmp_path)
    module = _module()
    module.run(match)
    artifact = read_artifact(PIPELINE["commentary"], artifact_path(match, "commentary"))
    assert artifact["schema_version"] == "commentary-rallies-v1"
    assert [item["segment_index"] for item in artifact["rallies"]] == [0, 1, 2]
    assert read_status(stage_path(match, "commentary")).status == StageStatus.COMPLETED
    output = capsys.readouterr().out
    assert "WARNING: FULL-MATCH COMMENTARY" in output
    assert "approximately 9-12" in output


def test_cli_selection_contract(monkeypatch):
    from modules.commentary import __main__ as cli

    with pytest.raises(SystemExit):
        cli.parse_args(["match"])
    with pytest.raises(SystemExit):
        cli.parse_args(["match", "--all", "--segment", "1"])
    args = cli.parse_args(["match", "--segment", "7", "--segment", "12"])
    assert args.segment == [7, 12] and not args.all

    calls = []

    class FakeModule:
        def run(self, match): calls.append(("all", match))
        def generate_segments(self, match, segments, progress):
            calls.append(("segments", match, segments, type(progress).__name__))
            return type("Result", (), {"unsupported_segments": []})()

    monkeypatch.setattr(cli, "CommentaryModule", FakeModule)
    cli.main(["match", "--segment", "7"])
    cli.main(["match", "--all"])
    assert calls == [
        ("segments", "match", [7], "CommentaryProgress"),
        ("all", "match"),
    ]


def test_runner_default_excludes_commentary_but_opt_in_runs_it(tmp_path, monkeypatch, capsys):
    import modules.runner as runner

    class Counting(BaseModule):
        dependencies = []
        optional_dependencies = []
        def __init__(self, name): self.name, self.runs = name, 0
        def check_ready(self, _): return True
        def get_output_path(self, match): return Path(match) / f"{self.name}.json"
        def _run(self, match, **_):
            self.runs += 1
            path = self.get_output_path(match)
            path.write_text("{}", encoding="utf-8")
            return StageResult(path)

    upstream, commentary = Counting("match_segmentation"), Counting("commentary")
    monkeypatch.setattr(
        runner, "available_modules",
        lambda: {"match_segmentation": upstream, "commentary": commentary},
    )
    monkeypatch.setattr(runner, "_reject_gapped_input_video", lambda _: None)

    assert runner.run_pipeline(tmp_path)
    assert upstream.runs == 1 and commentary.runs == 0
    output = capsys.readouterr().out
    assert "Commentary was not generated" in output
    assert "WARNING: FULL-MATCH COMMENTARY" not in output

    # Remove the fake upstream status so the second invocation exercises both stages.
    write_status(
        stage_path(tmp_path, "match_segmentation"),
        StageState(name="match_segmentation", status=StageStatus.PENDING),
    )
    assert runner.run_pipeline(tmp_path, with_commentary=True)
    assert commentary.runs == 1
