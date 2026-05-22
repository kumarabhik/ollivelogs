from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from app.pricing import PricingCatalog
from app.providers.anthropic import AnthropicProvider
from app.providers.base import (
    CompletionResult,
    CompletionUsage,
    GenerationRequest,
    Provider,
    ProviderStreamChunk,
)
from app.providers.gemini import GeminiProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.settings import Settings


@dataclass(slots=True)
class ProviderRegistry:
    providers: dict[str, Provider]
    demo_provider: Provider

    def get(self, provider_name: str) -> Provider:
        return self.providers[provider_name]

    def has(self, provider_name: str) -> bool:
        return provider_name in self.providers


class LocalDemoProvider(Provider):
    def __init__(self, chunk_delay_ms: int = 20) -> None:
        self.name = "demo"
        self._chunk_delay_ms = chunk_delay_ms

    async def complete(self, request: GenerationRequest) -> CompletionResult:
        text = self._render(request)
        token_count = self.count_tokens(text, request.model)
        return CompletionResult(
            text=text,
            usage=CompletionUsage(
                prompt_tokens=self.count_tokens(request.prompt, request.model),
                completion_tokens=token_count,
                total_tokens=self.count_tokens(request.prompt, request.model) + token_count,
                cost_usd=0.0,
            ),
            finish_reason="stop",
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderStreamChunk]:
        import asyncio

        rendered = self._render(request)
        for index in range(0, len(rendered), 18):
            if self._chunk_delay_ms > 0:
                await asyncio.sleep(self._chunk_delay_ms / 1000)
            yield ProviderStreamChunk(delta=rendered[index:index + 18])
        yield ProviderStreamChunk(
            usage=CompletionUsage(
                prompt_tokens=self.count_tokens(request.prompt, request.model),
                completion_tokens=self.count_tokens(rendered, request.model),
                total_tokens=self.count_tokens(request.prompt, request.model)
                + self.count_tokens(rendered, request.model),
                cost_usd=0.0,
            ),
            finish_reason="stop",
        )

    def count_tokens(self, text: str, model: str) -> int:
        del model
        stripped = text.strip()
        return len(stripped.split()) if stripped else 0

    def price(
        self,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> float | None:
        del model, prompt_tokens, completion_tokens
        return 0.0

    def _render(self, request: GenerationRequest) -> str:
        previous_assistant = next(
            (message.content for message in reversed(request.context) if message.role == "assistant"),
            "No prior assistant response.",
        )
        return (
            f"[{request.provider}/{request.model}] "
            f"Replying to: {request.prompt}\n"
            f"Context turns: {request.context_turns}\n"
            f"Previous assistant: {previous_assistant[:60]}"
        )


def build_provider_registry(
    settings: Settings,
    http_client: httpx.AsyncClient,
    pricing: PricingCatalog,
) -> ProviderRegistry:
    demo_provider = LocalDemoProvider(chunk_delay_ms=settings.stream_chunk_delay_ms)
    providers: dict[str, Provider] = {
        "demo": demo_provider,
        "openai": OpenAICompatibleProvider(
            name="openai",
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            http_client=http_client,
            pricing=pricing,
        ),
        "anthropic": AnthropicProvider(
            base_url=settings.anthropic_base_url,
            api_key=settings.anthropic_api_key,
            anthropic_version=settings.anthropic_version,
            http_client=http_client,
            pricing=pricing,
        ),
        "gemini": GeminiProvider(
            base_url=settings.google_base_url,
            api_key=settings.google_api_key,
            http_client=http_client,
            pricing=pricing,
        ),
        "deepseek": OpenAICompatibleProvider(
            name="deepseek",
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            http_client=http_client,
            pricing=pricing,
        ),
        "grok": OpenAICompatibleProvider(
            name="grok",
            base_url=settings.xai_base_url,
            api_key=settings.xai_api_key,
            http_client=http_client,
            pricing=pricing,
        ),
        "huggingface": OpenAICompatibleProvider(
            name="huggingface",
            base_url=settings.hf_base_url,
            api_key=settings.hf_token,
            http_client=http_client,
            pricing=pricing,
        ),
    }
    return ProviderRegistry(providers=providers, demo_provider=demo_provider)
