from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from ollivelogs import OlliveLogs
from ollivelogs.client import (
    _extract_anthropic_response,
    _extract_generic_response,
    _extract_model,
    _extract_openai_response,
    _infer_provider,
    _messages_preview,
)
from ollivelogs.tracing import _truncate, current_trace

pytestmark = pytest.mark.unit


class MiscClient:
    def __init__(self) -> None:
        self.info = SimpleNamespace(label="wrapped")

    def ping(self) -> str:
        return "pong"


class FailingOpenAIClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model: str, messages: list[dict[str, str]]) -> object:
        del model
        del messages
        raise RuntimeError("boom")


class AnthropicSdkClient:
    __module__ = "anthropic.sdk"


def _capture_batches(captured: list[list[dict[str, object]]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured.append(cast(list[dict[str, object]], payload["events"]))
        return httpx.Response(200, json={"accepted": len(payload["events"])})

    return httpx.MockTransport(handler)


def test_messages_preview_handles_text_blocks_and_invalid_values() -> None:
    preview = _messages_preview(
        [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"text": "Part"}, {"text": " two"}]},
            {"role": "tool", "content": [{"ignored": "block"}]},
        ]
    )

    assert preview == "user: Hello\nassistant: Part two"
    assert _messages_preview("not-a-list") == ""


def test_extract_openai_response_captures_tool_calls() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=5, total_tokens=12),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=[{"text": "Calling weather tool."}],
                    tool_calls=[
                        SimpleNamespace(
                            id="call_123",
                            type="function",
                            function=SimpleNamespace(
                                name="weather_lookup",
                                arguments='{"city":"Bengaluru"}',
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
    )

    extracted = _extract_openai_response(response)

    assert extracted.text_preview == "Calling weather tool."
    assert extracted.total_tokens == 12
    assert extracted.finish_reason == "tool_calls"
    assert extracted.extra == {
        "tool_calls": [
            {
                "id": "call_123",
                "type": "function",
                "name": "weather_lookup",
                "arguments": '{"city":"Bengaluru"}',
            }
        ]
    }


def test_extract_anthropic_response_captures_tool_use_blocks() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=9, output_tokens=3),
        stop_reason="tool_use",
        content=[
            SimpleNamespace(text=""),
            SimpleNamespace(
                type="tool_use",
                id="toolu_1",
                name="lookup",
                input={"query": "revenue"},
            ),
        ],
    )

    extracted = _extract_anthropic_response(response)

    assert extracted.prompt_tokens == 9
    assert extracted.completion_tokens == 3
    assert extracted.finish_reason == "tool_use"
    assert extracted.extra == {
        "tool_calls": [
            {
                "id": "toolu_1",
                "type": "tool_use",
                "name": "lookup",
                "input": {"query": "revenue"},
            }
        ]
    }


def test_extract_generic_response_and_model_helpers() -> None:
    extracted = _extract_generic_response({"hello": "world"})

    assert extracted.text_preview == "{'hello': 'world'}"
    assert _extract_model((), {"model": "gpt-4.1-mini"}) == "gpt-4.1-mini"
    assert _extract_model((), {"model": 123}) is None


def test_wrap_passthrough_for_non_tracked_members() -> None:
    sdk = OlliveLogs(endpoint="https://ingest.test/v1/logs", transport=_capture_batches([]))
    wrapped = sdk.wrap(MiscClient())

    assert wrapped.ping() == "pong"
    assert wrapped.info.label == "wrapped"
    sdk.close()


@pytest.mark.asyncio
async def test_trace_emits_single_error_event_and_resets_context() -> None:
    captured_batches: list[list[dict[str, object]]] = []
    sdk = OlliveLogs(
        endpoint="https://ingest.test/v1/logs",
        batch_size=1,
        flush_interval_ms=5,
        queue_max=10,
        transport=_capture_batches(captured_batches),
    )

    with pytest.raises(RuntimeError), sdk.trace(
        conversation_id="conv-error",
        user_id="user-error",
    ):
        assert current_trace() is not None
        await sdk.wrap(FailingOpenAIClient()).chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "Hello"}],
        )

    assert current_trace() is None
    assert sdk.flush()
    sdk.close()

    emitted = [event for batch in captured_batches for event in batch]
    assert len(emitted) == 1
    assert emitted[0]["status"] == "error"
    assert cast(dict[str, object], emitted[0]["error"])["kind"] == "RuntimeError"


def test_trace_emit_is_idempotent_and_records_tool_calls() -> None:
    captured_batches: list[list[dict[str, object]]] = []
    sdk = OlliveLogs(
        endpoint="https://ingest.test/v1/logs",
        batch_size=1,
        flush_interval_ms=5,
        queue_max=10,
        transport=_capture_batches(captured_batches),
    )
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Done",
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            type="function",
                            function=SimpleNamespace(name="search", arguments="{}"),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
    )

    span = sdk.trace(conversation_id="conv-tools", user_id="user-tools").__enter__()
    span.record_request(provider="openai", model="gpt-4.1-mini", request_preview="user: hi")
    span.set_output(response)
    span.emit()
    span.emit()
    span.__exit__(None, None, None)

    assert sdk.flush()
    sdk.close()

    emitted = [event for batch in captured_batches for event in batch]
    assert len(emitted) == 1
    extra = cast(dict[str, object], emitted[0]["extra"])
    assert extra["trace_kind"] == "context_manager"
    assert cast(list[object], extra["tool_calls"])[0] == {
        "id": "call_1",
        "type": "function",
        "name": "search",
        "arguments": "{}",
    }


def test_provider_inference_and_truncation_helpers() -> None:
    assert _infer_provider(AnthropicSdkClient(), ("messages", "create")) == "anthropic"
    assert _infer_provider(object(), ("chat", "completions", "create")) == "openai"
    assert _truncate("x" * 520, limit=10) == "xxxxxxx..."
