"""SDK-neutral commentary request/response types."""

from dataclasses import dataclass
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ProviderError(RuntimeError):
    """A component failure, never a successful empty tactical result."""

    def __init__(self, code: str, message: str, *, diagnostics: "ProviderDiagnostics | None" = None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: Annotated[int, Field(ge=0, strict=True)] | None = None
    output_tokens: Annotated[int, Field(ge=0, strict=True)] | None = None
    thought_tokens: Annotated[int, Field(ge=0, strict=True)] | None = None
    total_tokens: Annotated[int, Field(ge=0, strict=True)] | None = None


class ProviderDiagnostics(BaseModel):
    """Response metadata only; never carries prompts or partial generated text."""

    model_config = ConfigDict(extra="forbid")
    finish_reason: str | None = None
    finish_detail: str | None = None
    requested_model: str
    returned_model: str | None = None
    usage: TokenUsage | None = None
    candidate_count: Annotated[int, Field(ge=0, strict=True)]
    text_present: bool
    parsed_content_present: bool


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    model: str | None = None
    usage: TokenUsage | None = None


class LLMProvider(Protocol):
    def generate(
        self, *, system_prompt: str, user_prompt: str,
        response_schema: type[BaseModel],
    ) -> ProviderResponse: ...


def require_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("empty_response", "Provider returned no structured response text")
    return text
