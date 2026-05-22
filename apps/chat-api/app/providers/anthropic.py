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


class AnthropicProvider(Provider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        anthropic_version: str,
        http_client: httpx.AsyncClient,
        pricing: PricingCatalog,
    ) -> None:
        self.name = "anthropic"
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._anthropic_version = anthropic_version
        self._http_client = http_client
        self._pricing = pricing

    async def complete(self, request: GenerationRequest) -> CompletionResult:
        response = await self._http_client.post(
            f"{self._base_url}/v1/messages",
            headers=self._headers(),
            json=_to_anthropic_payload(request, stream=False),
        )
        data = await _json_or_raise(response)
        usage = _usage_from_response(self._pricing, request.model, data.get("usage"))
        return CompletionResult(
            text=_extract_anthropic_text(data.get("content", [])),
            usage=usage,
            finish_reason=data.get("stop_reason"),
            raw=data,
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderStreamChunk]:
        async with self._http_client.stream(
            "POST",
            f"{self._base_url}/v1/messages",
            headers=self._headers(),
            json=_to_anthropic_payload(request, stream=True),
        ) as response:
            if response.status_code >= 400:
                raise ProviderUpstreamError(f"anthropic returned HTTP {response.status_code}")

            event_name = ""
            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    event_name = line.removeprefix("event:").strip()
                    continue
                if not line.startswith("data:"):
                    continue

                payload = json.loads(line.removeprefix("data:").strip())
                if event_name == "content_block_delta":
                    delta = payload.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if isinstance(text, str) and text:
                            yield ProviderStreamChunk(delta=text)
                elif event_name == "message_delta":
                    usage_data = payload.get("usage")
                    if isinstance(usage_data, dict):
                        yield ProviderStreamChunk(
                            usage=_usage_from_response(
                                self._pricing,
                                request.model,
                                usage_data,
                            ),
                            finish_reason=payload.get("delta", {}).get("stop_reason"),
                        )
                elif event_name == "error":
                    raise ProviderUpstreamError("anthropic streaming error")

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
            raise ProviderConfigurationError("anthropic is missing an API key.")
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._anthropic_version,
            "content-type": "application/json",
        }


def _to_anthropic_payload(request: GenerationRequest, *, stream: bool) -> dict[str, Any]:
    system_messages = [message.content for message in request.context if message.role == "system"]
    messages = [
        {"role": _map_role(message.role), "content": message.content}
        for message in request.context
        if message.role != "system"
    ]
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "max_tokens": request.max_tokens,
        "stream": stream,
    }
    if system_messages:
        payload["system"] = "\n".join(system_messages)
    return payload


def _map_role(role: str) -> str:
    return "assistant" if role == "assistant" else "user"


def _extract_anthropic_text(content_blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in content_blocks:
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _usage_from_response(
    pricing: PricingCatalog,
    model: str,
    usage_data: dict[str, Any] | None,
) -> CompletionUsage:
    prompt_tokens = _maybe_int(usage_data, "input_tokens")
    completion_tokens = _maybe_int(usage_data, "output_tokens")
    total_tokens = (
        prompt_tokens + completion_tokens
        if prompt_tokens is not None and completion_tokens is not None
        else None
    )
    return CompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=pricing.cost_for("anthropic", model, prompt_tokens, completion_tokens),
    )


async def _json_or_raise(response: httpx.Response) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ProviderUpstreamError(f"anthropic returned HTTP {response.status_code}") from exc
    return dict(response.json())


def _maybe_int(data: dict[str, Any] | None, key: str) -> int | None:
    if not data:
        return None
    value = data.get(key)
    return value if isinstance(value, int) else None
