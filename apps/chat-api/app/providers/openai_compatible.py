from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.pricing import PricingCatalog
from app.providers.base import (
    CompletionResult,
    CompletionUsage,
    GenerationRequest,
    Provider,
    ProviderConfigurationError,
    ProviderStreamChunk,
    ProviderUpstreamError,
)


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None,
        http_client: httpx.AsyncClient,
        pricing: PricingCatalog,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http_client = http_client
        self._pricing = pricing

    async def complete(self, request: GenerationRequest) -> CompletionResult:
        payload = {
            "model": request.model,
            "messages": _to_openai_messages(request),
            "stream": False,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        response = await self._http_client.post(
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        )
        data = await _json_or_raise(response, self.name)

        choice = data["choices"][0]
        text = _extract_openai_text(choice["message"].get("content"))
        usage = _build_usage(self._pricing, self.name, request.model, data.get("usage"))
        return CompletionResult(
            text=text,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            raw=data,
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderStreamChunk]:
        payload = {
            "model": request.model,
            "messages": _to_openai_messages(request),
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        async with self._http_client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        ) as response:
            if response.status_code >= 400:
                raise ProviderUpstreamError(f"{self.name} returned HTTP {response.status_code}")

            async for payload_text in _iter_sse_data(response):
                if payload_text == "[DONE]":
                    break

                data = json.loads(payload_text)
                usage_data = data.get("usage")
                usage = (
                    _build_usage(self._pricing, self.name, request.model, usage_data)
                    if usage_data
                    else None
                )
                choice = data.get("choices", [{}])[0]
                delta = _extract_openai_text(choice.get("delta", {}).get("content"))
                finish_reason = choice.get("finish_reason")
                if delta or usage or finish_reason:
                    yield ProviderStreamChunk(
                        delta=delta,
                        usage=usage,
                        finish_reason=finish_reason,
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
        return self._pricing.cost_for(self.name, model, prompt_tokens, completion_tokens)

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise ProviderConfigurationError(f"{self.name} is missing an API key.")
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


def _to_openai_messages(request: GenerationRequest) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in request.context]


def _extract_openai_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
        return "".join(text_parts)
    return ""


def _build_usage(
    pricing: PricingCatalog,
    provider: str,
    model: str,
    usage_data: dict[str, Any] | None,
) -> CompletionUsage:
    prompt_tokens = _maybe_int(usage_data, "prompt_tokens")
    completion_tokens = _maybe_int(usage_data, "completion_tokens")
    total_tokens = _maybe_int(usage_data, "total_tokens")
    return CompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=pricing.cost_for(provider, model, prompt_tokens, completion_tokens),
    )


async def _json_or_raise(response: httpx.Response, provider_name: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ProviderUpstreamError(
            f"{provider_name} returned HTTP {response.status_code}"
        ) from exc
    return dict(response.json())


def _maybe_int(data: dict[str, Any] | None, key: str) -> int | None:
    if not data:
        return None
    value = data.get(key)
    return value if isinstance(value, int) else None


async def _iter_sse_data(response: httpx.Response) -> AsyncIterator[str]:
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        yield line.removeprefix("data:").strip()
