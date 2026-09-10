"""Offline provider contract tests; no credentials or network calls."""
from unittest.mock import Mock

import pytest
from google.genai import types

from modules.commentary.analysis.tactical_response import TacticalResponse
from modules.commentary.providers.base import ProviderError
from modules.commentary.providers.fake import FakeProvider
from modules.commentary.providers.gemini import GeminiConfig, GeminiProvider


def generate(provider):
    return provider.generate(system_prompt="system", user_prompt="payload", response_schema=TacticalResponse)


def sdk_response(text='{"segment_index":1,"facts":[]}', finish="STOP"):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text=text)]), finish_reason=finish)],
        model_version="test-version", usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=4, total_token_count=14))


def test_fake_deterministic_and_error_propagation():
    provider = FakeProvider('{"segment_index":1,"facts":[]}')
    assert generate(provider) == generate(provider)
    assert len(provider.calls) == 2
    error = ProviderError("test", "failure")
    with pytest.raises(ProviderError) as caught:
        generate(FakeProvider("", error=error))
    assert caught.value is error


@pytest.mark.parametrize("text", ["", " ", "\t\n"])
def test_empty_fake(text):
    with pytest.raises(ProviderError) as caught:
        generate(FakeProvider(text))
    assert caught.value.code == "empty_response"


def test_gemini_structured_single_request_metadata():
    client = Mock()
    client.models.generate_content.return_value = sdk_response()
    provider = GeminiProvider(GeminiConfig(model="explicit-model"), client=client)
    result = generate(provider)
    client.models.generate_content.assert_called_once()
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "explicit-model"
    assert kwargs["contents"] == "payload"
    assert kwargs["config"].response_json_schema == TacticalResponse.model_json_schema()
    assert kwargs["config"].response_mime_type == "application/json"
    assert kwargs["config"].automatic_function_calling.disable
    assert result.model == "test-version"
    assert result.usage.input_tokens == 10 and result.usage.total_tokens == 14
    provider.close()
    client.close.assert_not_called()


@pytest.mark.parametrize("response,code", [
    (sdk_response(""), "empty_response"),
    (sdk_response("{}", "MAX_TOKENS"), "incomplete_response"),
    (types.GenerateContentResponse(), "empty_response"),
])
def test_gemini_empty_incomplete(response, code):
    client = Mock()
    client.models.generate_content.return_value = response
    with pytest.raises(ProviderError) as caught:
        generate(GeminiProvider(GeminiConfig(model="test"), client=client))
    assert caught.value.code == code


def test_gemini_transport_error_is_sanitized():
    client = Mock()
    client.models.generate_content.side_effect = TimeoutError("secret API key URL")
    with pytest.raises(ProviderError) as caught:
        generate(GeminiProvider(GeminiConfig(model="test"), client=client))
    assert caught.value.code == "request_failed"
    assert "secret" not in str(caught.value)
    client.models.generate_content.assert_called_once()


def test_owned_client_timeout_retry_credentials_and_close(monkeypatch):
    import modules.commentary.providers.gemini as module
    factory = Mock()
    monkeypatch.setattr(module.genai, "Client", factory)
    monkeypatch.setattr(module, "get_gemini_api_key", lambda: "test-key")
    with GeminiProvider(GeminiConfig(model="test", timeout_seconds=2.5)):
        pass
    options = factory.call_args.kwargs["http_options"]
    assert options.timeout == 2500 and options.retry_options.attempts == 1
    factory.return_value.close.assert_called_once()
    monkeypatch.setattr(module, "get_gemini_api_key", lambda: None)
    with pytest.raises(ProviderError) as caught:
        GeminiProvider(GeminiConfig(model="test"))
    assert caught.value.code == "missing_credentials"


@pytest.mark.parametrize("kwargs", [{"model":" "}, {"timeout_seconds":True},
    {"timeout_seconds":float("inf")}, {"timeout_seconds":0}, {"max_output_tokens":True}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        GeminiConfig(**({"model":"test"} | kwargs))


@pytest.mark.parametrize("text", ["", '{"segment_index":1,"facts":[]}'])
def test_incomplete_diagnostics_and_redaction(text):
    response = sdk_response(text, "MAX_TOKENS")
    response.candidates[0].finish_message = "Limit reached: secret-key system payload"
    response.usage_metadata.thoughts_token_count = 7
    response.parsed = {"segment_index":1,"facts":[]}
    client = Mock()
    client.models.generate_content.return_value = response
    with pytest.raises(ProviderError) as caught:
        generate(GeminiProvider(GeminiConfig(model="test"), client=client, api_key="secret-key"))
    assert caught.value.code == "incomplete_response"
    d = caught.value.diagnostics
    assert d.finish_reason == "MAX_TOKENS"
    assert d.finish_detail == "Limit reached: [redacted] [redacted] [redacted]"
    assert d.returned_model == "test-version" and d.requested_model == "test"
    assert d.usage.model_dump() == dict(input_tokens=10,output_tokens=4,thought_tokens=7,total_tokens=14)
    assert d.candidate_count == 1 and d.text_present == bool(text)
    assert d.parsed_content_present
    assert text == "" or text not in d.model_dump_json()


def test_missing_optional_diagnostics():
    client = Mock()
    client.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[types.Candidate()])
    with pytest.raises(ProviderError) as caught:
        generate(GeminiProvider(GeminiConfig(model="test"),client=client))
    d = caught.value.diagnostics
    assert caught.value.code == "incomplete_response"
    assert d.finish_reason is None and d.finish_detail is None
    assert d.returned_model is None and d.usage is None
    assert not d.text_present and not d.parsed_content_present


def test_partial_response_never_reaches_grounding(monkeypatch):
    import modules.commentary.analysis.tactical_analyzer as module
    client = Mock()
    client.models.generate_content.return_value = sdk_response(finish="MAX_TOKENS")
    compact = Mock()
    compact.model_dump.return_value = {}
    analysis = Mock()
    analysis.model_dump.return_value = {}
    monkeypatch.setattr(module, "_validated_inputs", lambda *args: (compact,analysis))
    monkeypatch.setattr(module, "_evidence_catalog", lambda *args: {})
    parse = Mock(side_effect=AssertionError("Partial content must not be parsed"))
    monkeypatch.setattr(module, "_parse_response", parse)
    with pytest.raises(ProviderError) as caught:
        module.analyze_tactical_facts(provider=GeminiProvider(GeminiConfig(model="test"),client=client),
                                      compact_facts=compact)
    assert caught.value.code == "incomplete_response"
    parse.assert_not_called()
