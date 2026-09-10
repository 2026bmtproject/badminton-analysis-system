"""Explicit opt-in only; consumes prepared compact facts, never writes artifacts."""
import os
from pathlib import Path

import pytest


def _smoke_prerequisites():
    if os.environ.get("COMMENTARY_GEMINI_SMOKE") != "1":
        pytest.skip("opt-in real Gemini tactical smoke requires COMMENTARY_GEMINI_SMOKE=1")
    settings = {}
    for name in ("COMMENTARY_COMPACT_FACTS", "COMMENTARY_GEMINI_MODEL"):
        value = os.environ.get(name, "").strip()
        if not value:
            pytest.skip(f"Gemini smoke prerequisite missing: {name}")
        settings[name] = value
    # This opt-in test explicitly requires the provider's existing environment
    # credential, preventing accidental use of a developer's config.yaml key.
    from modules.common.config import GEMINI_API_KEY_ENV
    key = os.environ.get(GEMINI_API_KEY_ENV, "")
    if not key.strip():
        pytest.skip(f"Gemini smoke prerequisite missing: {GEMINI_API_KEY_ENV}")
    path = Path(settings["COMMENTARY_COMPACT_FACTS"])
    if not path.is_file():
        pytest.fail("Gemini smoke prerequisite: COMMENTARY_COMPACT_FACTS must name an existing file")
    return path, settings["COMMENTARY_GEMINI_MODEL"], key


@pytest.mark.skipif(os.environ.get("COMMENTARY_GEMINI_SMOKE") != "1",
                    reason="opt-in real Gemini tactical smoke")
def test_real_gemini_tactical_smoke():
    path, model, key = _smoke_prerequisites()
    from modules.commentary.analysis.tactical_analyzer import analyze_tactical_facts
    from modules.commentary.facts.schemas import CompactRallyFacts
    from modules.commentary.providers.gemini import GeminiConfig, GeminiProvider

    facts = CompactRallyFacts.model_validate_json(
        path.read_text(encoding="utf-8"))
    with GeminiProvider(GeminiConfig(model=model), api_key=key) as provider:
        result = analyze_tactical_facts(provider=provider, compact_facts=facts)
    assert result.segment_index == facts.segment_index
    # Empty is legitimate; do not reward fabricated insights in a smoke check.
    assert all(f.segment_index == facts.segment_index for f in result.facts)


@pytest.mark.parametrize("missing", ["COMMENTARY_GEMINI_SMOKE", "COMMENTARY_COMPACT_FACTS",
                                    "COMMENTARY_GEMINI_MODEL", "GEMINI_API_KEY"])
@pytest.mark.parametrize("value", [None, " "])
def test_smoke_missing_prerequisites(monkeypatch, missing, value):
    for name, configured in {"COMMENTARY_GEMINI_SMOKE":"1", "COMMENTARY_COMPACT_FACTS":__file__,
                            "COMMENTARY_GEMINI_MODEL":"offline-test", "GEMINI_API_KEY":"test-only"}.items():
        monkeypatch.setenv(name, configured)
    if value is None:
        monkeypatch.delenv(missing)
    else:
        monkeypatch.setenv(missing, value)
    with pytest.raises(pytest.skip.Exception, match=missing):
        _smoke_prerequisites()


def test_smoke_bad_path_and_provider_failure(monkeypatch):
    from modules.commentary.providers.base import ProviderError
    import modules.commentary.providers.gemini as module
    for name, value in {"COMMENTARY_GEMINI_SMOKE":"1", "COMMENTARY_COMPACT_FACTS":__file__ + ".missing",
                        "COMMENTARY_GEMINI_MODEL":"offline-test", "GEMINI_API_KEY":"test-only"}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(pytest.fail.Exception, match="must name an existing file"):
        _smoke_prerequisites()
    monkeypatch.setenv("COMMENTARY_COMPACT_FACTS", __file__)
    from modules.commentary.facts.schemas import CompactRallyFacts
    monkeypatch.setattr(CompactRallyFacts, "model_validate_json", lambda _: object())
    error = ProviderError("request_failed", "offline injected failure")
    def failing_provider(*args, **kwargs):
        raise error
    monkeypatch.setattr(module, "GeminiProvider", failing_provider)
    with pytest.raises(ProviderError) as caught:
        test_real_gemini_tactical_smoke()
    assert caught.value is error
