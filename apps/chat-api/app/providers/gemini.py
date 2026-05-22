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


class GeminiProvider(Provider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        http_client: httpx.AsyncClient,
        pricing: PricingCatalog,
    ) -> None:
        self.name = "gemini"
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http_client = http_client
        self._pricing = pricing

    async def complete(self, request: GenerationRequest) -> CompletionResult:
        response = await self._http_client.post(
            f"{self._base_url}/models/{request.model}:generateContent",
            params=self._params(),
            json=_to_gemini_payload(request),
        )
        data = await _json_or_raise(response)
        usage = _usage_from_response(self._pricing, request.model, data.get("usageMetadata"))
        return CompletionResult(
            text=_extract_gemini_text(data),
            usage=usage,
            finish_reason=_extract_finish_reason(data),
            raw=data,
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderStreamChunk]:
        emitted_text = ""
        async with self._http_client.stream(
            "POST",
            f"{self._base_url}/models/{request.model}:streamGenerateContent",
            params={**self._params(), "alt": "sse"},
            json=_to_gemini_payload(request),
        ) as response:
            if response.status_code >= 400:
                raise ProviderUpstreamError(f"gemini returned HTTP {response.status_code}")

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue

                data = json.loads(line.removeprefix("data:").strip())
                full_text = _extract_gemini_text(data)
                delta = full_text
                if full_text.startswith(emitted_text):
                    delta = full_text[len(emitted_text):]
                emitted_text = full_text

                usage_data = data.get("usageMetadata")
                usage = (
                    _usage_from_response(self._pricing, request.model, usage_data)
                    if isinstance(usage_data, dict)
                    else None
                )
                finish_reason = _extract_finish_reason(data)
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

    def _params(self) -> dict[str, str]:
        if not self._api_key:
            raise ProviderConfigurationError("gemini is missing an API key.")
        return {"key": self._api_key}


def _to_gemini_payload(request: GenerationRequest) -> dict[str, Any]:
    system_text = "\n".join(
        message.content for message in request.context if message.role == "system"
    )
    contents = [
        {"role": _map_role(message.role), "parts": [{"text": message.content}]}
        for message in request.context
        if message.role != "system"
    ]
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_tokens,
        },
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}
    return payload


def _map_role(role: str) -> str:
    return "model" if role == "assistant" else "user"


def _extract_gemini_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates", [])
    if not candidates:
        return ""
    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    text_parts: list[str] = []
    for part in parts:
        text = part.get("text")
        if isinstance(text, str):
            text_parts.append(text)
    return "".join(text_parts)


def _extract_finish_reason(data: dict[str, Any]) -> str | None:
    candidates = data.get("candidates", [])
    if not candidates:
        return None
    finish_reason = candidates[0].get("finishReason")
    return finish_reason if isinstance(finish_reason, str) else None


def _usage_from_response(
    pricing: PricingCatalog,
    model: str,
    usage_data: dict[str, Any] | None,
) -> CompletionUsage:
    prompt_tokens = _maybe_int(usage_data, "promptTokenCount")
    completion_tokens = _maybe_int(usage_data, "candidatesTokenCount")
    total_tokens = _maybe_int(usage_data, "totalTokenCount")
    return CompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=pricing.cost_for("gemini", model, prompt_tokens, completion_tokens),
    )


async def _json_or_raise(response: httpx.Response) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ProviderUpstreamError(f"gemini returned HTTP {response.status_code}") from exc
    return dict(response.json())


def _maybe_int(data: dict[str, Any] | None, key: str) -> int | None:
    if not data:
        return None
    value = data.get(key)
    return value if isinstance(value, int) else None
