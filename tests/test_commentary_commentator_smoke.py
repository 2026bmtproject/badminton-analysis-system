"""Explicit opt-in real Commentator smoke; never runs by default."""

import json
import os
from pathlib import Path
from time import perf_counter

import pytest


FLAG = "COMMENTARY_COMMENTATOR_SMOKE"


def _prerequisites():
    if os.environ.get(FLAG) != "1":
        pytest.skip(f"opt-in requires {FLAG}=1")
    from modules.common.config import GEMINI_API_KEY_ENV
    required = (
        "COMMENTARY_RALLY_FACT", "COMMENTARY_COMPACT_FACTS",
        "COMMENTARY_GEMINI_MODEL", "COMMENTARY_TOP_PLAYER",
        "COMMENTARY_COMMENTATOR_SMOKE_OUTPUT", GEMINI_API_KEY_ENV,
    )
    settings = {}
    for name in required:
        value = os.environ.get(name, "").strip()
        if not value:
            pytest.skip(f"Commentator smoke prerequisite missing: {name}")
        settings[name] = value
    for name in ("COMMENTARY_RALLY_FACT", "COMMENTARY_COMPACT_FACTS"):
        if not Path(settings[name]).is_file():
            pytest.fail(f"{name} must name an existing UTF-8 JSON file")
    if settings["COMMENTARY_TOP_PLAYER"] not in ("a", "b"):
        pytest.fail("COMMENTARY_TOP_PLAYER must be a or b from human confirmation")
    return settings, GEMINI_API_KEY_ENV


class _Utf8Capture:
    def __init__(self, provider, role, raw_output):
        self.provider, self.role = provider, role
        self.raw_output, self.calls = Path(raw_output), 0
        self.record = None

    def generate(self, **kwargs):
        from modules.commentary.providers.base import ProviderError
        if self.calls:
            raise AssertionError("Commentator smoke exceeded one request")
        self.calls += 1
        started = perf_counter()
        try:
            response = self.provider.generate(**kwargs)
        except ProviderError as exc:
            self.record = dict(role=self.role, status="provider_error",
                latency_seconds=perf_counter()-started,
                error_code=exc.code,
                diagnostics=exc.diagnostics.model_dump(mode="json") if exc.diagnostics else None)
            raise
        self.raw_output.write_text(response.text, encoding="utf-8")
        self.record = dict(role=self.role, status="success", finish_reason="STOP",
            latency_seconds=perf_counter()-started, returned_model=response.model,
            usage=response.usage.model_dump(mode="json") if response.usage else None,
            parsed_structured_content_present=bool(response.text.strip()),
            raw_output=str(self.raw_output))
        return response


@pytest.mark.skipif(os.environ.get(FLAG) != "1", reason="opt-in real Gemini Commentator smoke")
def test_real_gemini_commentator_smoke():
    settings, key_name = _prerequisites()
    from modules.commentary.facts.observations import TacticalObservation
    from modules.commentary.facts.schemas import CompactRallyFacts
    from modules.commentary.identity import CourtPositionToPlayer
    from modules.commentary.providers.gemini import GeminiConfig, GeminiProvider
    from modules.commentary.schemas import RallyFact
    from modules.commentary.services import CommentaryService

    rally = RallyFact.model_validate_json(Path(settings["COMMENTARY_RALLY_FACT"]).read_text(encoding="utf-8"))
    compact = CompactRallyFacts.model_validate_json(Path(settings["COMMENTARY_COMPACT_FACTS"]).read_text(encoding="utf-8"))
    observation_path = os.environ.get("COMMENTARY_TACTICAL_OBSERVATIONS", "").strip()
    observations = []
    if observation_path:
        raw = json.loads(Path(observation_path).read_text(encoding="utf-8"))
        observations = [TacticalObservation.model_validate(item) for item in raw]
    top = settings["COMMENTARY_TOP_PLAYER"]
    diagnostics_path = Path(settings["COMMENTARY_COMMENTATOR_SMOKE_OUTPUT"])
    raw_commentator_path = diagnostics_path.with_name(diagnostics_path.stem + "-commentator-raw.json")
    raw_reviewer_path = diagnostics_path.with_name(diagnostics_path.stem + "-reviewer-raw.json")
    rally_output_path = diagnostics_path.with_name(diagnostics_path.stem + "-commentary-rally.json")
    config = GeminiConfig(model=settings["COMMENTARY_GEMINI_MODEL"], max_output_tokens=4096)
    with (GeminiProvider(config, api_key=settings[key_name]) as provider,
          GeminiProvider(config, api_key=settings[key_name]) as review_provider):
        capture = _Utf8Capture(provider, "commentator", raw_commentator_path)
        review_capture = _Utf8Capture(review_provider, "commentary_semantic_reviewer", raw_reviewer_path)
        try:
            result = CommentaryService(
                provider=capture, reviewer=review_capture,
                requested_model=settings["COMMENTARY_GEMINI_MODEL"],
                reviewer_requested_model=settings["COMMENTARY_GEMINI_MODEL"],
            ).generate(
                    rally_fact=rally, compact_facts=compact,
                    court_position_to_player=CourtPositionToPlayer(
                        top=top, bottom="b" if top == "a" else "a"),
                    tactical_observations=observations,
                )
        except Exception:
            diagnostics_path.write_text(json.dumps(dict(
                status="failed", commentator=capture.record, reviewer=review_capture.record,
                commentator_calls=capture.calls, reviewer_calls=review_capture.calls,
            ), ensure_ascii=False, indent=2), encoding="utf-8")
            raise
    rally_output_path.write_text(
        json.dumps(result.commentary.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8")
    final = dict(
        status="success", commentator=capture.record, reviewer=review_capture.record,
        event_count=len(compact.events), eligible_event_count=len(result.commentary.events),
        commentator_calls=capture.calls, reviewer_calls=review_capture.calls,
        supplied_tactical_observation_ids=result.supplied_tactical_observation_ids,
        final_commentary_rally=str(rally_output_path), result=result.model_dump(mode="json"),
    )
    diagnostics_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=True))
    assert capture.calls == review_capture.calls == 1


@pytest.mark.parametrize("missing", [
    FLAG, "COMMENTARY_RALLY_FACT", "COMMENTARY_COMPACT_FACTS",
    "COMMENTARY_GEMINI_MODEL", "COMMENTARY_TOP_PLAYER",
    "COMMENTARY_COMMENTATOR_SMOKE_OUTPUT", "GEMINI_API_KEY",
])
def test_smoke_missing_prerequisites_skip(monkeypatch, missing):
    configured = {
        FLAG: "1", "COMMENTARY_RALLY_FACT": __file__,
        "COMMENTARY_COMPACT_FACTS": __file__, "COMMENTARY_GEMINI_MODEL": "offline",
        "COMMENTARY_TOP_PLAYER": "b", "COMMENTARY_COMMENTATOR_SMOKE_OUTPUT": __file__ + ".json",
        "GEMINI_API_KEY": "test-only",
    }
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    with pytest.raises(pytest.skip.Exception, match=missing):
        _prerequisites()
