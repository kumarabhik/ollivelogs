from __future__ import annotations

import uuid
from typing import Any


def build_event(
    *,
    event_id: str | None = None,
    conversation_id: str | None = None,
    request_preview: str = "user: hello there",
    response_preview: str = "assistant: hi there",
) -> dict[str, Any]:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_schema_version": 1,
        "ts": "2026-05-22T09:00:00Z",
        "conversation_id": conversation_id or str(uuid.uuid4()),
        "user_id": None,
        "client": "py-sdk",
        "sdk_version": "0.1.0",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "request": {
            "messages_preview": request_preview,
            "stream": True,
            "temperature": 0.3,
            "max_tokens": 256,
        },
        "response": {
            "text_preview": response_preview,
            "finish_reason": "stop",
        },
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 24,
            "total_tokens": 36,
        },
        "timing": {
            "latency_ms": 742,
            "ttft_ms": 188,
        },
        "status": "ok",
        "error": None,
        "cost_usd": 0.000123,
        "request_id": "req_ingest_test",
        "extra": {
            "traceparent": "00-test",
        },
    }
