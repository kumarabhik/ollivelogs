from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

MessageRole = Literal["system", "user", "assistant", "tool"]


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderUpstreamError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class ContextMessage:
    role: MessageRole
    content: str


@dataclass(slots=True, frozen=True)
class GenerationRequest:
    provider: str
    model: str
    prompt: str
    context: Sequence[ContextMessage]
    context_turns: int
    max_tokens: int
    temperature: float = 0.2


@dataclass(slots=True, frozen=True)
class CompletionUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(slots=True, frozen=True)
class CompletionResult:
    text: str
    usage: CompletionUsage
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class ProviderStreamChunk:
    delta: str = ""
    usage: CompletionUsage | None = None
    finish_reason: str | None = None


class Provider(Protocol):
    name: str

    async def complete(self, request: GenerationRequest) -> CompletionResult: ...

    def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderStreamChunk]: ...

    def count_tokens(self, text: str, model: str) -> int: ...

    def price(
        self,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> float | None: ...
