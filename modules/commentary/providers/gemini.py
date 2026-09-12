"""One structured Gemini request; no automatic retries or model fallback."""

import math
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import types
from pydantic import BaseModel

from modules.common.config import get_gemini_api_key
from .base import ProviderDiagnostics, ProviderError, ProviderResponse, TokenUsage, require_text


@dataclass(frozen=True)
class GeminiConfig:
    # Explicit model selection avoids silent movement of a "latest" alias.
    model: str
    timeout_seconds: float = 30.0
    max_output_tokens: int = 2048

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be nonblank")
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds < .001):
            raise ValueError("timeout_seconds must be finite and at least 0.001")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 32:
            raise ValueError("max_output_tokens must be an integer >= 32")


class _Models(Protocol):
    def generate_content(self, *, model: str, contents: str,
                         config: types.GenerateContentConfig) -> types.GenerateContentResponse: ...


class _Client(Protocol):
    models: _Models

    def close(self) -> None: ...


class GeminiProvider:
    def __init__(self, config: GeminiConfig, *, api_key: str | None = None,
                 client: _Client | None = None):
        self.config = config
        self._diagnostic_key = api_key
        self._owns_client = client is None
        if client is None:
            key = api_key if api_key is not None else get_gemini_api_key()
            self._diagnostic_key = key
            if not isinstance(key, str) or not key.strip():
                raise ProviderError("missing_credentials", "Set GEMINI_API_KEY or gemini_api_key in config.yaml")
            client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=int(config.timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ))
        self._client = client

    def generate(self, *, system_prompt: str, user_prompt: str,
                 response_schema: type[BaseModel]) -> ProviderResponse:
        try:
            response = self._client.models.generate_content(
                model=self.config.model, contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_json_schema=response_schema.model_json_schema(),
                    max_output_tokens=self.config.max_output_tokens,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            text = response.text
        except Exception as exc:
            # Transport exceptions can contain a URL/API key. Do not echo them.
            status = getattr(exc, "code", None)
            suffix = f" (HTTP {status})" if type(status) is int else ""
            raise ProviderError("request_failed", f"Gemini request failed or timed out{suffix}") from None
        candidates = response.candidates or []
        raw_usage = response.usage_metadata
        usage = None
        if raw_usage is not None:
            usage = TokenUsage(
                input_tokens=raw_usage.prompt_token_count,
                output_tokens=raw_usage.candidates_token_count,
                thought_tokens=raw_usage.thoughts_token_count,
                total_tokens=raw_usage.total_token_count,
            )
        candidate = candidates[0] if candidates else None
        detail = candidate.finish_message if candidate is not None else None
        if detail is not None:
            # SDK finish detail is diagnostic prose, not generated content.
            # Redact request strings and credentials before retaining it.
            for sensitive in (self._diagnostic_key, system_prompt, user_prompt):
                if sensitive:
                    detail = detail.replace(sensitive, "[redacted]")
            detail = detail[:512]
        diagnostics = ProviderDiagnostics(
            finish_reason=candidate.finish_reason.value if candidate is not None and candidate.finish_reason is not None else None,
            finish_detail=detail, requested_model=self.config.model,
            returned_model=response.model_version, usage=usage,
            candidate_count=len(candidates), text_present=isinstance(text, str) and bool(text.strip()),
            parsed_content_present=response.parsed is not None,
        )
        if candidate is not None and candidate.finish_reason != types.FinishReason.STOP:
            raise ProviderError("incomplete_response", "Gemini response did not finish normally",
                                diagnostics=diagnostics)
        if not diagnostics.text_present:
            raise ProviderError("empty_response", "Provider returned no structured response text",
                                diagnostics=diagnostics)
        text = require_text(text)
        return ProviderResponse(text, response.model_version or self.config.model, usage)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
