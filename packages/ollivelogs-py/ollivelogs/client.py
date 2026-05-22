from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

import httpx

from ollivelogs.shipper import AsyncBatchShipper
from ollivelogs.tracing import ExtractedResponse, TraceSpan, current_trace

ClientT = TypeVar("ClientT")

_OPENAI_CREATE_PATH = ("chat", "completions", "create")
_ANTHROPIC_CREATE_PATH = ("messages", "create")
_TRACKED_METHOD_PATHS = {_OPENAI_CREATE_PATH, _ANTHROPIC_CREATE_PATH}


class OlliveLogs:
    def __init__(
        self,
        *,
        endpoint: str,
        sdk_version: str = "0.1.0",
        client_name: str = "py-sdk",
        batch_size: int = 50,
        flush_interval_ms: int = 200,
        queue_max: int = 1000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.sdk_version = sdk_version
        self.client_name = client_name
        self._shipper = AsyncBatchShipper(
            endpoint=endpoint,
            batch_size=batch_size,
            flush_interval_ms=flush_interval_ms,
            queue_max=queue_max,
            transport=transport,
        )

    @property
    def dropped_events(self) -> int:
        return self._shipper.dropped_events

    def trace(
        self,
        *,
        conversation_id: str,
        user_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        client_name: str | None = None,
    ) -> TraceSpan:
        return TraceSpan(
            self,
            conversation_id=conversation_id,
            user_id=user_id,
            provider=provider,
            model=model,
            client_name=client_name or self.client_name,
        )

    def wrap(self, client: ClientT) -> ClientT:
        return cast(ClientT, WrappedClientProxy(self, client, root_client=client, path=()))

    def enqueue_event(self, event: dict[str, object]) -> None:
        self._shipper.enqueue(event)

    def flush(self, timeout_seconds: float = 5.0) -> bool:
        return self._shipper.flush(timeout_seconds)

    def close(self, timeout_seconds: float = 5.0) -> None:
        self._shipper.close(timeout_seconds)

    def extract_response(self, provider: str | None, response: object) -> ExtractedResponse:
        normalized_provider = (provider or "").lower()
        if normalized_provider == "anthropic":
            return _extract_anthropic_response(response)
        if normalized_provider == "openai":
            return _extract_openai_response(response)
        return _extract_generic_response(response)


class WrappedClientProxy:
    def __init__(
        self,
        sdk: OlliveLogs,
        target: object,
        *,
        root_client: object,
        path: tuple[str, ...],
    ) -> None:
        self._sdk = sdk
        self._target = target
        self._root_client = root_client
        self._path = path

    def __getattr__(self, name: str) -> object:
        attr = getattr(self._target, name)
        new_path = (*self._path, name)
        if callable(attr) and new_path in _TRACKED_METHOD_PATHS:
            return TrackedCallProxy(
                self._sdk,
                attr,
                root_client=self._root_client,
                path=new_path,
            )
        if callable(attr):
            return attr
        if not hasattr(attr, "__dict__"):
            return attr
        return WrappedClientProxy(
            self._sdk,
            attr,
            root_client=self._root_client,
            path=new_path,
        )


class TrackedCallProxy:
    def __init__(
        self,
        sdk: OlliveLogs,
        target: object,
        *,
        root_client: object,
        path: tuple[str, ...],
    ) -> None:
        self._sdk = sdk
        self._target = target
        self._root_client = root_client
        self._path = path

    def __call__(self, *args: object, **kwargs: object) -> object:
        trace = current_trace()
        callable_target = cast(Callable[..., object], self._target)
        if trace is None:
            return callable_target(*args, **kwargs)

        provider = _infer_provider(self._root_client, self._path)
        model = _extract_model(args, kwargs)
        request_preview = _extract_request_preview(self._path, args, kwargs)
        trace.record_request(provider=provider, model=model, request_preview=request_preview)

        started_perf = time.perf_counter()
        try:
            result = callable_target(*args, **kwargs)
        except Exception as exc:
            trace.record_error(exc, latency_ms=_latency_ms(started_perf))
            raise

        if inspect.isawaitable(result):
            return self._await_and_record(cast(Awaitable[object], result), trace, started_perf)

        trace.record_response(result, latency_ms=_latency_ms(started_perf))
        return result

    async def _await_and_record(
        self,
        awaitable: Awaitable[object],
        trace: TraceSpan,
        started_perf: float,
    ) -> object:
        try:
            result = await awaitable
        except Exception as exc:
            trace.record_error(exc, latency_ms=_latency_ms(started_perf))
            raise
        trace.record_response(result, latency_ms=_latency_ms(started_perf))
        return result


def _infer_provider(root_client: object, path: tuple[str, ...]) -> str:
    module_name = type(root_client).__module__.lower()
    class_name = type(root_client).__name__.lower()
    if "anthropic" in module_name or "anthropic" in class_name or path == _ANTHROPIC_CREATE_PATH:
        return "anthropic"
    return "openai"


def _extract_model(args: tuple[object, ...], kwargs: dict[str, object]) -> str | None:
    model_value = kwargs.get("model")
    return model_value if isinstance(model_value, str) else None


def _extract_request_preview(
    path: tuple[str, ...],
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> str:
    del path
    del args
    messages = kwargs.get("messages")
    return _messages_preview(messages)


def _messages_preview(messages: object) -> str:
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for item in messages:
        if isinstance(item, dict):
            role = item.get("role")
            content = item.get("content")
            role_text = role if isinstance(role, str) else "user"
            if isinstance(content, str):
                parts.append(f"{role_text}: {content}")
            elif isinstance(content, list):
                text_parts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
                if text_parts:
                    parts.append(f"{role_text}: {''.join(text_parts)}")
    return "\n".join(parts)


def _extract_openai_response(response: object) -> ExtractedResponse:
    usage = _get_value(response, "usage")
    prompt_tokens = _int_value(usage, "prompt_tokens")
    completion_tokens = _int_value(usage, "completion_tokens")
    total_tokens = _int_value(usage, "total_tokens")
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    choices = _get_value(response, "choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        message = _get_value(first_choice, "message")
        text_preview = _coerce_openai_content(_get_value(message, "content"))
        finish_reason = _string_value(first_choice, "finish_reason")
        extra = _openai_response_extra(message)
        return ExtractedResponse(
            text_preview=text_preview,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            cost_usd=0.0,
            extra=extra,
        )
    return _extract_generic_response(response)


def _extract_anthropic_response(response: object) -> ExtractedResponse:
    usage = _get_value(response, "usage")
    prompt_tokens = _int_value(usage, "input_tokens")
    completion_tokens = _int_value(usage, "output_tokens")
    total_tokens = prompt_tokens + completion_tokens
    content = _get_value(response, "content")
    text_parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            text = _get_value(block, "text")
            if isinstance(text, str):
                text_parts.append(text)
    extra = _anthropic_response_extra(content)
    return ExtractedResponse(
        text_preview="".join(text_parts),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=_string_value(response, "stop_reason"),
        cost_usd=0.0,
        extra=extra,
    )


def _extract_generic_response(response: object) -> ExtractedResponse:
    return ExtractedResponse(
        text_preview=str(response),
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        finish_reason=None,
        cost_usd=0.0,
    )


def _get_value(obj: object, key: str) -> object:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _string_value(obj: object, key: str) -> str | None:
    value = _get_value(obj, key)
    return value if isinstance(value, str) else None


def _int_value(obj: object, key: str) -> int:
    value = _get_value(obj, key)
    return value if isinstance(value, int) else 0


def _coerce_openai_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _openai_response_extra(message: object) -> dict[str, object] | None:
    tool_calls = _get_value(message, "tool_calls")
    if not isinstance(tool_calls, list):
        return None

    normalized_calls: list[dict[str, object]] = []
    for tool_call in tool_calls:
        function_payload = _get_value(tool_call, "function")
        item: dict[str, object] = {}
        tool_call_id = _string_value(tool_call, "id")
        tool_call_type = _string_value(tool_call, "type")
        function_name = _string_value(function_payload, "name")
        function_arguments = _string_value(function_payload, "arguments")
        if tool_call_id is not None:
            item["id"] = tool_call_id
        if tool_call_type is not None:
            item["type"] = tool_call_type
        if function_name is not None:
            item["name"] = function_name
        if function_arguments is not None:
            item["arguments"] = function_arguments
        if item:
            normalized_calls.append(item)

    if not normalized_calls:
        return None
    return {"tool_calls": normalized_calls}


def _anthropic_response_extra(content: object) -> dict[str, object] | None:
    if not isinstance(content, list):
        return None

    normalized_calls: list[dict[str, object]] = []
    for block in content:
        if _string_value(block, "type") != "tool_use":
            continue
        item: dict[str, object] = {}
        tool_call_id = _string_value(block, "id")
        name = _string_value(block, "name")
        input_payload = _get_value(block, "input")
        if tool_call_id is not None:
            item["id"] = tool_call_id
        item["type"] = "tool_use"
        if name is not None:
            item["name"] = name
        if isinstance(input_payload, dict):
            item["input"] = input_payload
        if item:
            normalized_calls.append(item)

    if not normalized_calls:
        return None
    return {"tool_calls": normalized_calls}


def _latency_ms(started_perf: float) -> int:
    return int((time.perf_counter() - started_perf) * 1000)
