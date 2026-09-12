"""Explicit opt-in v2 production path, at most two billable requests."""

import json
import os
from pathlib import Path
from time import perf_counter

import pytest


def _prerequisites():
    if os.environ.get("COMMENTARY_GEMINI_V2_SMOKE") != "1":
        pytest.skip("opt-in requires COMMENTARY_GEMINI_V2_SMOKE=1")
    from modules.common.config import GEMINI_API_KEY_ENV
    settings = {}
    for name in ("COMMENTARY_COMPACT_FACTS", "COMMENTARY_GEMINI_MODEL", GEMINI_API_KEY_ENV):
        value = os.environ.get(name, "").strip()
        if not value:
            pytest.skip(f"V2 Gemini smoke prerequisite missing: {name}")
        settings[name] = value
    path = Path(settings["COMMENTARY_COMPACT_FACTS"])
    if not path.is_file():
        pytest.fail("COMMENTARY_COMPACT_FACTS must name an existing compact JSON file")
    return path, settings["COMMENTARY_GEMINI_MODEL"], settings[GEMINI_API_KEY_ENV]


class _Capture:
    def __init__(self, provider):
        self.provider, self.calls = provider, 0

    def generate(self, **kwargs):
        from modules.commentary.providers.base import ProviderError
        if self.calls >= 2:
            raise AssertionError("v2 smoke exceeded two logical calls")
        self.calls += 1
        started = perf_counter()
        try:
            response = self.provider.generate(**kwargs)
        except ProviderError as exc:
            print(json.dumps(dict(call=self.calls, latency_seconds=perf_counter()-started,
                error_code=exc.code,
                diagnostics=exc.diagnostics.model_dump(mode="json") if exc.diagnostics else None)))
            raise
        print(json.dumps(dict(call=self.calls, latency_seconds=perf_counter()-started,
            returned_model=response.model, usage=response.usage.model_dump(mode="json") if response.usage else None)))
        return response


@pytest.mark.skipif(os.environ.get("COMMENTARY_GEMINI_V2_SMOKE") != "1", reason="opt-in real Gemini v2 smoke")
def test_real_gemini_observations_smoke():
    path, model, key = _prerequisites()
    from modules.commentary.analysis.observation_analyzer import analyze_tactical_observations
    from modules.commentary.facts.schemas import CompactRallyFacts
    from modules.commentary.providers.gemini import GeminiConfig, GeminiProvider
    compact = CompactRallyFacts.model_validate_json(path.read_text(encoding="utf-8"))
    with (GeminiProvider(GeminiConfig(model=model, max_output_tokens=4096), api_key=key) as provider,
          GeminiProvider(GeminiConfig(model=model, max_output_tokens=4096), api_key=key) as review_provider):
        capture = _Capture(provider)
        review_capture = _Capture(review_provider)
        result = analyze_tactical_observations(generator=capture, reviewer=review_capture, compact_facts=compact)
    print(json.dumps(dict(requested_model=model, raw_proposal_count=result.raw_proposal_count,
        grounding_rejection_count=len(result.grounding_rejections),
        verdict_counts={v: sum(r.verdict == v for r in result.review_verdicts) for v in ("pass", "reject", "uncertain")},
        result=result.model_dump(mode="json")), ensure_ascii=False))
    assert capture.calls == 1
    assert review_capture.calls == (1 if result.review else 0)


@pytest.mark.parametrize("missing", ["COMMENTARY_GEMINI_V2_SMOKE", "COMMENTARY_COMPACT_FACTS",
                                     "COMMENTARY_GEMINI_MODEL", "GEMINI_API_KEY"])
@pytest.mark.parametrize("value", [None, " "])
def test_missing_prerequisite_skips(monkeypatch, missing, value):
    for name, configured in {"COMMENTARY_GEMINI_V2_SMOKE":"1", "COMMENTARY_COMPACT_FACTS":__file__,
                            "COMMENTARY_GEMINI_MODEL":"offline", "GEMINI_API_KEY":"test-only"}.items():
        monkeypatch.setenv(name, configured)
    if value is None:
        monkeypatch.delenv(missing)
    else:
        monkeypatch.setenv(missing, value)
    with pytest.raises(pytest.skip.Exception, match=missing):
        _prerequisites()


def test_capture_preserves_provider_failure(capsys):
    from modules.commentary.providers.base import ProviderDiagnostics, ProviderError
    from modules.commentary.providers.fake import FakeProvider
    error = ProviderError("incomplete_response", "Incomplete", diagnostics=ProviderDiagnostics(
        requested_model="offline", finish_reason="MAX_TOKENS", candidate_count=1,
        text_present=True, parsed_content_present=False))
    capture = _Capture(FakeProvider("", error=error))
    with pytest.raises(ProviderError) as caught:
        capture.generate(system_prompt="private", user_prompt="private", response_schema=None)
    assert caught.value is error and capture.calls == 1
    output = capsys.readouterr().out
    assert "MAX_TOKENS" in output and "private" not in output
