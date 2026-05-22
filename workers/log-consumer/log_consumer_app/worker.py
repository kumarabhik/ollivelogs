from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

import asyncpg
from clickhouse_connect.driver.client import Client
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from log_consumer_app.metrics import (
    INGEST_EVENTS_TOTAL,
    WORKER_BATCH_DURATION_SECONDS,
    WORKER_BATCH_SIZE,
    WORKER_LAG_SECONDS,
)
from log_consumer_app.notifications import WorkerNotifier
from log_consumer_app.observability import get_worker_tracer
from log_consumer_app.redaction import TextRedactor
from log_consumer_app.repository import build_clickhouse_row, insert_clickhouse_log, insert_message
from log_consumer_app.schemas import InferenceLogEvent, RedactedEventRecord, ensure_utc
from log_consumer_app.settings import Settings

log = logging.getLogger("log-consumer.worker")


@dataclass(slots=True)
class StreamEntry:
    stream_id: str
    payload: str


class LogConsumerWorker:
    """Background worker that drains Redis Streams into Postgres + ClickHouse."""

    def __init__(
        self,
        *,
        settings: Settings,
        postgres_pool: asyncpg.Pool,
        redis_client: Redis,
        clickhouse_client: Client,
    ) -> None:
        self._settings = settings
        self._postgres_pool = postgres_pool
        self._redis_client = redis_client
        self._clickhouse_client = clickhouse_client
        self._redactor = TextRedactor(settings)
        self._notifier = WorkerNotifier(
            settings=settings,
            postgres_pool=postgres_pool,
            redis_client=redis_client,
        )
        self._tracer = get_worker_tracer()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.ensure_consumer_group()
        self._task = asyncio.create_task(self.consume_forever(), name="ollivelogs-log-consumer")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
        await self._notifier.aclose()

    async def ensure_consumer_group(self) -> None:
        try:
            await self._redis_client.xgroup_create(
                name=self._settings.event_bus_stream,
                groupname=self._settings.event_bus_consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def consume_forever(self) -> None:
        while not self._stop_event.is_set():
            batch = await self._read_batch()
            if not batch:
                continue

            started_at = time.perf_counter()
            WORKER_BATCH_SIZE.observe(len(batch))
            WORKER_LAG_SECONDS.set(_estimate_lag_seconds(batch))
            for entry in batch:
                await self._process_entry(entry)
            WORKER_BATCH_DURATION_SECONDS.observe(time.perf_counter() - started_at)

    async def _read_batch(self) -> list[StreamEntry]:
        response = await self._redis_client.xreadgroup(
            groupname=self._settings.event_bus_consumer_group,
            consumername=self._settings.event_bus_consumer_name,
            streams={self._settings.event_bus_stream: ">"},
            count=self._settings.worker_batch_size,
            block=self._settings.worker_block_ms,
        )
        if not response:
            return []
        _, entries = response[0]
        return [
            StreamEntry(
                stream_id=stream_id,
                payload=str(fields["payload"]),
            )
            for stream_id, fields in entries
            if "payload" in fields
        ]

    async def _process_entry(self, entry: StreamEntry) -> None:
        try:
            event = InferenceLogEvent.model_validate_json(entry.payload)
            trace_context = _extract_trace_context(event)
            with self._tracer.start_as_current_span(
                "log-consumer.process_event",
                context=trace_context,
            ):
                record = self._redact_event(event)
                if not _skip_message_persist(event):
                    await insert_message(
                        self._postgres_pool,
                        record,
                        store_raw=self._settings.store_raw,
                        raw_encryption_key=self._settings.raw_encryption_key,
                    )
                await insert_clickhouse_log(self._clickhouse_client, build_clickhouse_row(record))
                await self._notify_budget_alert(record)
                await self._ack(entry.stream_id)
                INGEST_EVENTS_TOTAL.labels(status="ok").inc()
                log.info(
                    "processed_event",
                    extra={
                        "request_id": record.request_id,
                        "stream_id": entry.stream_id,
                        "status": event.status,
                    },
                )
        except ValidationError as exc:
            await self._send_to_dlq(entry, f"validation_error:{exc.errors()!r}")
        except Exception as exc:  # pragma: no cover - integration covers the behavior
            await self._send_to_dlq(entry, f"{type(exc).__name__}:{exc}")

    def _redact_event(self, event: InferenceLogEvent) -> RedactedEventRecord:
        input_preview = self._redactor.redact_preview(event.request.messages_preview).text
        output_preview = self._redactor.redact_preview(event.response.text_preview).text
        return RedactedEventRecord(
            event=event.model_copy(update={"ts": ensure_utc(event.ts)}),
            input_preview=input_preview,
            output_preview=output_preview,
            request_id=event.request_id or _request_id_from_extra(event),
            raw_content=_raw_message_content(event),
        )

    async def _ack(self, stream_id: str) -> None:
        await self._redis_client.xack(
            self._settings.event_bus_stream,
            self._settings.event_bus_consumer_group,
            stream_id,
        )

    async def _send_to_dlq(self, entry: StreamEntry, reason: str) -> None:
        await self._redis_client.xadd(
            self._settings.event_bus_dlq,
            {
                "payload": entry.payload,
                "error_reason": reason,
                "source_stream": self._settings.event_bus_stream,
                "source_stream_id": entry.stream_id,
            },
        )
        await self._ack(entry.stream_id)
        INGEST_EVENTS_TOTAL.labels(status="dlq").inc()
        log.warning(
            "event_dlq",
            extra={
                "stream_id": entry.stream_id,
                "reason": reason,
            },
        )
        await self._notify_dlq(entry, reason)

    async def _notify_dlq(self, entry: StreamEntry, reason: str) -> None:
        try:
            await self._notifier.notify_dlq(
                stream_id=entry.stream_id,
                reason=reason,
                payload=entry.payload,
            )
        except Exception as exc:
            log.warning("dlq_notification_failed", exc_info=exc)

    async def _notify_budget_alert(self, record: RedactedEventRecord) -> None:
        try:
            await self._notifier.maybe_send_budget_alert(record)
        except Exception as exc:
            log.warning(
                "budget_alert_failed",
                extra={
                    "request_id": record.request_id,
                    "user_id": str(record.event.user_id) if record.event.user_id is not None else "",
                },
                exc_info=exc,
            )


def _estimate_lag_seconds(batch: Sequence[StreamEntry]) -> float:
    if not batch:
        return 0.0
    oldest_ms = min(int(entry.stream_id.split("-", 1)[0]) for entry in batch)
    return max(0.0, (time.time() * 1000 - oldest_ms) / 1000.0)


def _request_id_from_extra(event: InferenceLogEvent) -> str:
    if event.extra is None:
        return ""
    value = event.extra.get("request_id")
    if isinstance(value, str):
        return value
    return ""


def _raw_message_content(event: InferenceLogEvent) -> str:
    if event.response.text_preview:
        return event.response.text_preview
    if event.request.messages_preview:
        return event.request.messages_preview
    if event.error is not None:
        return event.error.message
    return ""


def _skip_message_persist(event: InferenceLogEvent) -> bool:
    if event.extra is None:
        return False
    value = event.extra.get("skip_message_persist")
    return value is True


def _extract_trace_context(event: InferenceLogEvent) -> object | None:
    if event.extra is None:
        return None
    traceparent = event.extra.get("traceparent")
    if not isinstance(traceparent, str) or traceparent == "":
        return None
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    except ImportError:
        return None
    return TraceContextTextMapPropagator().extract({"traceparent": traceparent})
