"""Fixed responses and recorded requests for offline tests."""

from dataclasses import dataclass, field

from pydantic import BaseModel

from .base import ProviderError, ProviderResponse, TokenUsage, require_text


@dataclass(frozen=True)
class PromptCall:
    system_prompt: str
    user_prompt: str
    response_schema: type[BaseModel]


@dataclass
class FakeProvider:
    response: str
    error: ProviderError | None = None
    model: str = "fake"
    usage: TokenUsage | None = None
    calls: list[PromptCall] = field(default_factory=list, init=False)

    def generate(self, *, system_prompt, user_prompt, response_schema) -> ProviderResponse:
        self.calls.append(PromptCall(system_prompt, user_prompt, response_schema))
        if self.error is not None:
            raise self.error
        return ProviderResponse(require_text(self.response), self.model, self.usage)
