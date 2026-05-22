from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.main import _run_completion, _run_stream
from app.providers import (
    CompletionResult,
    CompletionUsage,
    GenerationRequest,
    Provider,
    ProviderStreamChunk,
    ProviderUpstreamError,
)
from app.providers.registry import LocalDemoProvider, ProviderRegistry
from app.settings import Settings

pytestmark = pytest.mark.unit


class FakeProvider(Provider):
    def __init__(
        self,
        *,
        name: str,
        complete_result: CompletionResult | None = None,
        complete_error: Exception | None = None,
        stream_chunks: list[ProviderStreamChunk] | None = None,
        stream_error: Exception | None = None,
        expected_model: str | None = None,
    ) -> None:
        self.name = name
        self._complete_result = complete_result
        self._complete_error = complete_error
        self._stream_chunks = stream_chunks or []
        self._stream_error = stream_error
        self._expected_model = expected_model

    async def complete(self, request: GenerationRequest) -> CompletionResult:
        if self._expected_model is not None:
            assert request.model == self._expected_model
            assert request.provider == self.name
        if self._complete_error is not None:
            raise self._complete_error
        assert self._complete_result is not None
        return self._complete_result

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderStreamChunk]:
        if self._expected_model is not None:
            assert request.model == self._expected_model
            assert request.provider == self.name
        if self._stream_error is not None:
            raise self._stream_error
        for chunk in self._stream_chunks:
            yield chunk

    def count_tokens(self, text: str, model: str) -> int:
        del text
        del model
        return 1

    def price(
        self,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> float | None:
        del model
        del prompt_tokens
        del completion_tokens
        return 0.0


@pytest.mark.asyncio
async def test_run_completion_retries_with_failover_provider() -> None:
    generation = GenerationRequest(
        provider="openai",
        model="gpt-4.1-mini",
        prompt="hello",
        context=[],
        context_turns=1,
        max_tokens=64,
    )
    openai_provider = FakeProvider(
        name="openai",
        complete_error=ProviderUpstreamError("openai returned HTTP 503"),
    )
    anthropic_provider = FakeProvider(
        name="anthropic",
        complete_result=CompletionResult(
            text="Fallback worked",
            usage=CompletionUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            finish_reason="stop",
        ),
        expected_model="claude-sonnet-4-20250514",
    )
    registry = ProviderRegistry(
        providers={
            "openai": openai_provider,
            "anthropic": anthropic_provider,
        },
        demo_provider=LocalDemoProvider(chunk_delay_ms=0),
    )
    settings = Settings(
        app_env="prod",
        provider_failover_enabled=True,
        provider_failover_map="openai=anthropic",
        anthropic_default_model="claude-sonnet-4-20250514",
    )

    execution = await _run_completion(
        provider_name="openai",
        model_name="gpt-4.1-mini",
        provider=openai_provider,
        generation=generation,
        provider_registry=registry,
        app_settings=settings,
    )

    assert execution.provider_name == "anthropic"
    assert execution.model_name == "claude-sonnet-4-20250514"
    assert execution.result.text == "Fallback worked"


@pytest.mark.asyncio
async def test_run_stream_retries_with_failover_provider_before_first_chunk() -> None:
    generation = GenerationRequest(
        provider="openai",
        model="gpt-4.1-mini",
        prompt="hello",
        context=[],
        context_turns=1,
        max_tokens=64,
    )
    openai_provider = FakeProvider(
        name="openai",
        stream_error=ProviderUpstreamError("openai returned HTTP 502"),
    )
    anthropic_provider = FakeProvider(
        name="anthropic",
        stream_chunks=[
            ProviderStreamChunk(delta="Hello"),
            ProviderStreamChunk(
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                finish_reason="stop",
            ),
        ],
        expected_model="claude-sonnet-4-20250514",
    )
    registry = ProviderRegistry(
        providers={
            "openai": openai_provider,
            "anthropic": anthropic_provider,
        },
        demo_provider=LocalDemoProvider(chunk_delay_ms=0),
    )
    settings = Settings(
        app_env="prod",
        provider_failover_enabled=True,
        provider_failover_map="openai=anthropic",
        anthropic_default_model="claude-sonnet-4-20250514",
    )

    chunks = [
        chunk
        async for chunk in _run_stream(
            provider_name="openai",
            model_name="gpt-4.1-mini",
            provider=openai_provider,
            generation=generation,
            provider_registry=registry,
            app_settings=settings,
        )
    ]

    assert chunks[0].provider_name == "anthropic"
    assert chunks[0].model_name == "claude-sonnet-4-20250514"
    assert chunks[0].chunk.delta == "Hello"
