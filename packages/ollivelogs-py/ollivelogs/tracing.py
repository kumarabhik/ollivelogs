from __future__ import annotations

import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from ollivelogs.client import OlliveLogs

_CURRENT_TRACE: ContextVar[TraceSpan | None] = ContextVar(
    "ollivelogs_current_trace",
    default=None,
)


@dataclass(slots=True)
class ExtractedResponse:
    text_preview: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str | None
    cost_usd: float
    extra: dict[str, object] | None = None


class TraceSpan:
    def __init__(
        self,
        sdk: OlliveLogs,
        *,
        conversation_id: str,
        user_id: str | None,
        provider: str | None,
        model: str | None,
        client_name: str,
    ) -> None:
        self._sdk = sdk
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.provider = provider
        self.model = model
        self.client_name = client_name
        self.started_at = datetime.now(UTC)
        self._started_perf = time.perf_counter()
        self._token: Token[TraceSpan | None] | None = None
        self._request_preview = ""
        self._response_preview = ""
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._finish_reason: str | None = None
        self._cost_usd = 0.0
        self._extra: dict[str, object] = {}
        self._status = "ok"
        self._error_kind: str | None = None
        self._error_message: str | None = None
        self._latency_ms: int | None = None
        self._emitted = False

    def __enter__(self) -> TraceSpan:
        self._token = _CURRENT_TRACE.set(self)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, exc_tb: object) -> None:
        del exc_tb
        if exc_type is not None and exc is not None:
            self.record_error(exc)
        self.emit()
        if self._token is not None:
            _CURRENT_TRACE.reset(self._token)

    def set_output(self, response: object) -> None:
        extracted = self._sdk.extract_response(self.provider, response)
        self._apply_extracted_response(extracted)

    def record_request(self, *, provider: str, model: str | None, request_preview: str) -> None:
        self.provider = provider
        if model is not None:
            self.model = model
        if request_preview:
            self._request_preview = request_preview

    def record_response(self, response: object, *, latency_ms: int) -> None:
        extracted = self._sdk.extract_response(self.provider, response)
        self._apply_extracted_response(extracted)
        self._latency_ms = latency_ms
        self._status = "ok"

    def record_error(self, error: BaseException, *, latency_ms: int | None = None) -> None:
        self._status = "error"
        self._error_kind = type(error).__name__
        self._error_message = str(error)
        self._latency_ms = latency_ms or self._elapsed_latency_ms()

    def emit(self) -> None:
        if self._emitted:
            return
        self._emitted = True
        latency_ms = self._latency_ms or self._elapsed_latency_ms()
        event = {
            "event_id": str(uuid4()),
            "event_schema_version": 1,
            "ts": self.started_at.isoformat().replace("+00:00", "Z"),
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "client": self.client_name,
            "sdk_version": self._sdk.sdk_version,
            "provider": self.provider or "unknown",
            "model": self.model or "unknown",
            "request": {
                "messages_preview": _truncate(self._request_preview),
                "stream": False,
                "temperature": None,
                "max_tokens": None,
            },
            "response": {
                "text_preview": _truncate(self._response_preview),
                "finish_reason": self._finish_reason,
            },
            "usage": {
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._total_tokens,
            },
            "timing": {
                "latency_ms": latency_ms,
                "ttft_ms": 0,
            },
            "status": self._status,
            "error": (
                {
                    "kind": self._error_kind or "RuntimeError",
                    "message": self._error_message or "",
                }
                if self._status != "ok"
                else None
            ),
            "cost_usd": self._cost_usd,
            "request_id": f"req_{uuid4().hex[:12]}",
            "extra": self._event_extra(),
        }
        self._sdk.enqueue_event(event)

    def _apply_extracted_response(self, extracted: ExtractedResponse) -> None:
        self._response_preview = extracted.text_preview
        self._prompt_tokens = extracted.prompt_tokens
        self._completion_tokens = extracted.completion_tokens
        self._total_tokens = extracted.total_tokens
        self._finish_reason = extracted.finish_reason
        self._cost_usd = extracted.cost_usd
        if extracted.extra is not None:
            self._extra = dict(extracted.extra)

    def _elapsed_latency_ms(self) -> int:
        return int((time.perf_counter() - self._started_perf) * 1000)

    def _event_extra(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "trace_kind": "context_manager",
        }
        payload.update(self._extra)
        return payload


def current_trace() -> TraceSpan | None:
    return _CURRENT_TRACE.get()


def _truncate(text: str, limit: int = 512) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
