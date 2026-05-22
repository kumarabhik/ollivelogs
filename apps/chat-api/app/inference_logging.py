from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

from app.providers import CompletionUsage
from app.settings import Settings


class InferenceLogger(Protocol):
    def enqueue_event(self, event: dict[str, object]) -> None: ...

    def flush(self, timeout_seconds: float = 5.0) -> bool: ...

    def close(self, timeout_seconds: float = 5.0) -> None: ...


class OlliveLogsFactory(Protocol):
    def __call__(
        self,
        *,
        endpoint: str,
        sdk_version: str = "0.1.0",
        client_name: str = "py-sdk",
        batch_size: int = 50,
        flush_interval_ms: int = 200,
        queue_max: int = 1000,
    ) -> InferenceLogger: ...


def build_inference_logger(settings: Settings) -> InferenceLogger | None:
    if settings.ollive_ingest_url is None or settings.ollive_ingest_url == "":
        return None

    OlliveLogs = _load_ollivelogs_class()
    return OlliveLogs(
        endpoint=settings.ollive_ingest_url,
        sdk_version="0.1.0",
        client_name="chat-api",
        batch_size=settings.ollive_sdk_batch_size,
        flush_interval_ms=settings.ollive_sdk_flush_ms,
        queue_max=settings.ollive_sdk_queue_max,
    )


def emit_chat_inference_event(
    *,
    logger: InferenceLogger | None,
    settings: Settings,
    conversation_id: UUID,
    user_id: UUID | None,
    provider: str,
    model: str,
    request_preview: str,
    response_preview: str,
    usage: CompletionUsage | None,
    latency_ms: int,
    ttft_ms: int,
    status: str,
    stream: bool,
    request_id: str | None,
    error_kind: str | None = None,
    error_message: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    if logger is None:
        return

    prompt_tokens = usage.prompt_tokens if usage and usage.prompt_tokens is not None else 0
    completion_tokens = (
        usage.completion_tokens if usage and usage.completion_tokens is not None else 0
    )
    total_tokens = usage.total_tokens if usage and usage.total_tokens is not None else (
        prompt_tokens + completion_tokens
    )
    cost_usd = usage.cost_usd if usage and usage.cost_usd is not None else 0.0

    event_extra = {
        "message_role": "assistant",
        "skip_message_persist": True,
        "source": "chat-api",
        "traceparent": current_traceparent(),
    }
    if extra is not None:
        event_extra.update(extra)

    event = {
        "event_id": str(uuid4()),
        "event_schema_version": 1,
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "conversation_id": str(conversation_id),
        "user_id": str(user_id) if user_id is not None else None,
        "client": "chat-api",
        "sdk_version": "0.1.0",
        "provider": provider,
        "model": model,
        "request": {
            "messages_preview": _truncate(request_preview),
            "stream": stream,
            "temperature": None,
            "max_tokens": settings.provider_max_tokens,
        },
        "response": {
            "text_preview": _truncate(response_preview),
            "finish_reason": "cancelled" if status == "cancelled" else None,
        },
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "timing": {
            "latency_ms": max(latency_ms, 0),
            "ttft_ms": max(ttft_ms, 0),
        },
        "status": status,
        "error": (
            {
                "kind": error_kind or "ProviderError",
                "message": error_message or "",
            }
            if error_kind is not None or error_message is not None
            else None
        ),
        "cost_usd": max(cost_usd, 0.0),
        "request_id": request_id,
        "extra": {key: value for key, value in event_extra.items() if value is not None},
    }
    logger.enqueue_event(event)
    logger.flush(settings.ollive_sdk_flush_timeout_seconds)


def current_traceparent() -> str | None:
    try:
        from opentelemetry import trace
        from opentelemetry.trace.span import format_span_id, format_trace_id
    except ImportError:
        return None

    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return None
    trace_flags = int(span_context.trace_flags) & 0x01
    return (
        f"00-{format_trace_id(span_context.trace_id)}-"
        f"{format_span_id(span_context.span_id)}-{trace_flags:02x}"
    )


def _load_ollivelogs_class() -> OlliveLogsFactory:
    try:
        from ollivelogs import OlliveLogs

        return cast(OlliveLogsFactory, OlliveLogs)
    except ImportError:
        repo_root = Path(__file__).resolve().parents[3]
        sdk_root = repo_root / "packages" / "ollivelogs-py"
        if str(sdk_root) not in sys.path:
            sys.path.insert(0, str(sdk_root))
        from ollivelogs import OlliveLogs

        return cast(OlliveLogsFactory, OlliveLogs)


def _truncate(text: str, limit: int = 512) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
