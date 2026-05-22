from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx
import pytest
from app.pricing import PricingCatalog
from app.providers import ContextMessage, GenerationRequest, build_provider_registry
from app.providers.anthropic import AnthropicProvider
from app.providers.base import CompletionResult
from app.providers.gemini import GeminiProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.registry import LocalDemoProvider
from app.settings import Settings

pytestmark = pytest.mark.unit
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"


def _fixture_json(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_local_demo_provider_complete_and_stream_match() -> None:
    provider = LocalDemoProvider(chunk_delay_ms=0)
    request = GenerationRequest(
        provider="demo",
        model="demo-model",
        prompt="Summarize this",
        context=[
            ContextMessage(role="user", content="Hello"),
            ContextMessage(role="assistant", content="Hi there"),
            ContextMessage(role="user", content="Summarize this"),
        ],
        context_turns=2,
        max_tokens=128,
    )

    complete_result = await provider.complete(request)
    streamed_text = "".join([chunk.delta async for chunk in provider.stream(request)])

    assert streamed_text == complete_result.text
    assert "Context turns: 2" in complete_result.text
    assert "[demo/demo-model]" in complete_result.text


@pytest.mark.asyncio
async def test_openai_compatible_provider_parses_complete_and_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            payload = json.loads(request.content.decode())
            if payload["stream"]:
                return httpx.Response(200, text=_fixture_text("openai_stream.txt"))
            return httpx.Response(200, json=_fixture_json("openai_complete.json"))
        raise AssertionError("unexpected request")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        provider = OpenAICompatibleProvider(
            name="openai",
            base_url="https://example.test",
            api_key="test-key",
            http_client=client,
            pricing=PricingCatalog.from_repo_file(),
        )
        request = GenerationRequest(
            provider="openai",
            model="gpt-4.1-mini",
            prompt="Hi",
            context=[ContextMessage(role="user", content="Hi")],
            context_turns=1,
            max_tokens=64,
        )

        result = await provider.complete(request)
        stream_text = ""
        usage_total = None
        async for chunk in provider.stream(request):
            stream_text += chunk.delta
            if chunk.usage is not None:
                usage_total = chunk.usage.total_tokens

    assert isinstance(result, CompletionResult)
    assert result.text == "Hello world"
    assert stream_text == "Hello world"
    assert usage_total == 4
    assert result.usage.cost_usd is not None


@pytest.mark.asyncio
async def test_anthropic_provider_parses_complete_and_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        if payload["stream"]:
            return httpx.Response(200, text=_fixture_text("anthropic_stream.txt"))
        return httpx.Response(200, json=_fixture_json("anthropic_complete.json"))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        provider = AnthropicProvider(
            base_url="https://example.test",
            api_key="anthropic-key",
            anthropic_version="2023-06-01",
            http_client=client,
            pricing=PricingCatalog.from_repo_file(),
        )
        request = GenerationRequest(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            prompt="Hi",
            context=[ContextMessage(role="user", content="Hi")],
            context_turns=1,
            max_tokens=64,
        )

        result = await provider.complete(request)
        stream_text = "".join([chunk.delta async for chunk in provider.stream(request)])

    assert result.text == "Hello Anthropic"
    assert stream_text == "Hello Anthropic"
    assert result.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_gemini_provider_parses_complete_and_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":streamGenerateContent"):
            return httpx.Response(200, text=_fixture_text("gemini_stream.txt"))
        return httpx.Response(200, json=_fixture_json("gemini_complete.json"))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        provider = GeminiProvider(
            base_url="https://example.test",
            api_key="gemini-key",
            http_client=client,
            pricing=PricingCatalog.from_repo_file(),
        )
        request = GenerationRequest(
            provider="gemini",
            model="gemini-2.5-flash",
            prompt="Hi",
            context=[ContextMessage(role="user", content="Hi")],
            context_turns=1,
            max_tokens=64,
        )

        result = await provider.complete(request)
        stream_text = "".join([chunk.delta async for chunk in provider.stream(request)])

    assert result.text == "Hello Gemini"
    assert stream_text == "Hello Gemini"
    assert result.usage.total_tokens == 4


@pytest.mark.asyncio
async def test_registry_builds_all_requested_providers() -> None:
    pricing = PricingCatalog.from_repo_file()
    settings = Settings(
        openai_api_key="openai-key",
        anthropic_api_key="anthropic-key",
        google_api_key="google-key",
        deepseek_api_key="deepseek-key",
        xai_api_key="xai-key",
        hf_token="hf-key",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))) as client:
        registry = build_provider_registry(settings, client, pricing)

    assert registry.has("openai")
    assert registry.has("anthropic")
    assert registry.has("gemini")
    assert registry.has("deepseek")
    assert registry.has("grok")
    assert registry.has("huggingface")
    assert pricing.cost_for("openai", "gpt-4.1-mini", 1000, 500) is not None
