from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from ollivelogs import OlliveLogs

pytestmark = pytest.mark.unit


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model: str, messages: list[dict[str, str]]) -> object:
        assert model == "gpt-4.1-mini"
        assert messages
        assert messages[0]["role"] == "user"
        assert isinstance(messages[0]["content"], str)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Hi there"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        )


class FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, *, model: str, messages: list[dict[str, str]]) -> object:
        assert model == "claude-sonnet-4-20250514"
        assert messages[0]["role"] == "user"
        return SimpleNamespace(
            content=[SimpleNamespace(text="Hello from Claude")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=4, output_tokens=3),
        )


def _capture_transport(
    captured_batches: list[list[dict[str, object]]],
    *,
    fail_first_n: int = 0,
) -> httpx.MockTransport:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] <= fail_first_n:
            return httpx.Response(503, json={"detail": "retry me"})
        payload = json.loads(request.content.decode("utf-8"))
        captured_batches.append(payload["events"])
        return httpx.Response(
            200,
            json={
                "accepted": len(payload["events"]),
                "deduped": 0,
                "event_ids": [event["event_id"] for event in payload["events"]],
                "stream_ids": [],
            },
        )

    return httpx.MockTransport(handler)


def test_wrap_openai_client_emits_trace_event() -> None:
    captured_batches: list[list[dict[str, object]]] = []
    sdk = OlliveLogs(
        endpoint="https://ingest.test/v1/logs",
        batch_size=1,
        flush_interval_ms=5,
        queue_max=10,
        transport=_capture_transport(captured_batches),
    )

    with sdk.trace(conversation_id="conv-openai", user_id="user-1") as span:
        response = sdk.wrap(FakeOpenAIClient()).chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "Hello"}],
        )
        span.set_output(response)

    assert sdk.flush()
    sdk.close()

    emitted_events = [event for batch in captured_batches for event in batch]
    assert len(emitted_events) == 1
    emitted_event = emitted_events[0]
    emitted_response = cast(dict[str, object], emitted_event["response"])
    emitted_usage = cast(dict[str, object], emitted_event["usage"])
    assert emitted_event["provider"] == "openai"
    assert emitted_event["model"] == "gpt-4.1-mini"
    assert emitted_response["text_preview"] == "Hi there"
    assert emitted_usage["total_tokens"] == 5


def test_wrap_anthropic_client_emits_trace_event() -> None:
    captured_batches: list[list[dict[str, object]]] = []
    sdk = OlliveLogs(
        endpoint="https://ingest.test/v1/logs",
        batch_size=1,
        flush_interval_ms=5,
        queue_max=10,
        transport=_capture_transport(captured_batches),
    )

    with sdk.trace(conversation_id="conv-anthropic", user_id="user-2"):
        sdk.wrap(FakeAnthropicClient()).messages.create(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello Claude"}],
        )

    assert sdk.flush()
    sdk.close()

    emitted_event = captured_batches[0][0]
    emitted_usage = cast(dict[str, object], emitted_event["usage"])
    emitted_response = cast(dict[str, object], emitted_event["response"])
    assert emitted_event["provider"] == "anthropic"
    assert emitted_usage["prompt_tokens"] == 4
    assert emitted_usage["completion_tokens"] == 3
    assert emitted_response["text_preview"] == "Hello from Claude"


def test_shipper_retries_failed_batches_before_success() -> None:
    captured_batches: list[list[dict[str, object]]] = []
    sdk = OlliveLogs(
        endpoint="https://ingest.test/v1/logs",
        batch_size=1,
        flush_interval_ms=5,
        queue_max=10,
        transport=_capture_transport(captured_batches, fail_first_n=2),
    )

    with sdk.trace(conversation_id="conv-retry", user_id="user-3"):
        sdk.wrap(FakeOpenAIClient()).chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "Retry me"}],
        )

    assert sdk.flush(timeout_seconds=10.0)
    sdk.close()

    emitted_events = [event for batch in captured_batches for event in batch]
    assert len(emitted_events) == 1
    assert sdk.dropped_events == 0


def test_shipper_drops_oldest_when_queue_is_full() -> None:
    def failing_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    sdk = OlliveLogs(
        endpoint="https://ingest.test/v1/logs",
        batch_size=50,
        flush_interval_ms=1000,
        queue_max=5,
        transport=httpx.MockTransport(failing_transport),
    )

    wrapped = sdk.wrap(FakeOpenAIClient())
    for index in range(20):
        with sdk.trace(conversation_id=f"conv-drop-{index}", user_id="user-4"):
            wrapped.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": f"message {index}"}],
            )

    sdk.close(timeout_seconds=10.0)
    assert sdk.dropped_events >= 20


def test_one_thousand_events_are_ingested_or_counted_as_dropped() -> None:
    captured_batches: list[list[dict[str, object]]] = []
    sdk = OlliveLogs(
        endpoint="https://ingest.test/v1/logs",
        batch_size=50,
        flush_interval_ms=5,
        queue_max=1000,
        transport=_capture_transport(captured_batches),
    )

    wrapped = sdk.wrap(FakeOpenAIClient())
    for index in range(1000):
        with sdk.trace(conversation_id=f"conv-{index}", user_id="bulk-user"):
            wrapped.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": f"Hello {index}"}],
            )

    assert sdk.flush(timeout_seconds=20.0)
    sdk.close(timeout_seconds=10.0)

    ingested = sum(len(batch) for batch in captured_batches)
    assert ingested + sdk.dropped_events == 1000


def test_shipper_forwards_traceparent_and_request_id_headers() -> None:
    captured_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(dict(request.headers))
        payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "accepted": len(payload["events"]),
                "deduped": 0,
                "event_ids": [event["event_id"] for event in payload["events"]],
                "stream_ids": [],
            },
        )

    sdk = OlliveLogs(
        endpoint="https://ingest.test/v1/logs",
        batch_size=1,
        flush_interval_ms=5,
        queue_max=10,
        transport=httpx.MockTransport(handler),
    )

    sdk.enqueue_event(
        {
            "event_id": "evt-1",
            "request_id": "req-123",
            "extra": {"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"},
        }
    )

    assert sdk.flush()
    sdk.close()

    assert captured_headers
    headers = {key.lower(): value for key, value in captured_headers[0].items()}
    assert headers["traceparent"] == "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    assert headers["x-request-id"] == "req-123"
