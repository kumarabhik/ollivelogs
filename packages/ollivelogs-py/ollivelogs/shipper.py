from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

import httpx
from prometheus_client import Counter

SDK_DROPPED_EVENTS_TOTAL = Counter(
    "ollivelogs_dropped_events_total",
    "Number of OlliveLogs SDK events dropped before successful ingestion.",
)


@dataclass(slots=True)
class ShipperStats:
    dropped_events: int = 0
    sent_events: int = 0


class AsyncBatchShipper:
    """Background async shipper that batches SDK events to the ingest API."""

    def __init__(
        self,
        *,
        endpoint: str,
        batch_size: int,
        flush_interval_ms: int,
        queue_max: int,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_ms / 1000
        self._queue_max = queue_max
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._transport = transport

        self._queue: deque[dict[str, object]] = deque()
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run_worker,
            daemon=True,
            name="ollivelogs-shipper",
        )
        self._first_enqueued_at: float | None = None
        self._in_flight = False
        self._closed = False
        self._stats = ShipperStats()
        self._thread.start()

    @property
    def dropped_events(self) -> int:
        with self._lock:
            return self._stats.dropped_events

    @property
    def sent_events(self) -> int:
        with self._lock:
            return self._stats.sent_events

    def enqueue(self, event: Mapping[str, object]) -> None:
        with self._lock:
            if self._closed:
                return
            if len(self._queue) >= self._queue_max:
                self._queue.popleft()
                self._stats.dropped_events += 1
                SDK_DROPPED_EVENTS_TOTAL.inc()
            if not self._queue:
                self._first_enqueued_at = time.monotonic()
            self._queue.append(dict(event))
        self._wakeup.set()

    def flush(self, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                if not self._queue and not self._in_flight:
                    return True
            self._wakeup.set()
            time.sleep(0.01)
        return False

    def close(self, timeout_seconds: float = 5.0) -> None:
        with self._lock:
            self._closed = True
        self._stop_event.set()
        self._wakeup.set()
        self._thread.join(timeout_seconds)

    def _run_worker(self) -> None:
        asyncio.run(self._worker_loop())

    async def _worker_loop(self) -> None:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            while True:
                batch = self._pop_ready_batch(force=self._stop_event.is_set())
                if batch:
                    await self._send_or_drop_batch(client, batch)
                    continue

                if self._stop_event.is_set():
                    with self._lock:
                        if not self._queue and not self._in_flight:
                            break

                self._wakeup.wait(timeout=self._flush_interval_seconds)
                self._wakeup.clear()

    def _pop_ready_batch(self, *, force: bool) -> list[dict[str, object]]:
        now = time.monotonic()
        with self._lock:
            if not self._queue:
                self._first_enqueued_at = None
                return []

            flush_due = (
                self._first_enqueued_at is not None
                and now - self._first_enqueued_at >= self._flush_interval_seconds
            )
            if not force and len(self._queue) < self._batch_size and not flush_due:
                return []

            batch: list[dict[str, object]] = []
            while self._queue and len(batch) < self._batch_size:
                batch.append(self._queue.popleft())
            self._first_enqueued_at = time.monotonic() if self._queue else None
            self._in_flight = True
            return batch

    async def _send_or_drop_batch(
        self,
        client: httpx.AsyncClient,
        batch: list[dict[str, object]],
    ) -> None:
        try:
            if await self._send_batch_with_retry(client, batch):
                with self._lock:
                    self._stats.sent_events += len(batch)
                return
            with self._lock:
                self._stats.dropped_events += len(batch)
            SDK_DROPPED_EVENTS_TOTAL.inc(len(batch))
        finally:
            with self._lock:
                self._in_flight = False

    async def _send_batch_with_retry(
        self,
        client: httpx.AsyncClient,
        batch: list[dict[str, object]],
    ) -> bool:
        payload = {"events": batch}
        headers = _build_headers_for_batch(batch)
        for attempt in range(self._max_retries + 1):
            try:
                with _suppress_instrumentation():
                    response = await client.post(self._endpoint, json=payload, headers=headers)
                if response.status_code < 400:
                    return True
            except httpx.HTTPError:
                pass

            if attempt == self._max_retries:
                return False
            await asyncio.sleep(_retry_delay(attempt))
        return False


def _retry_delay(attempt: int) -> float:
    delay: float = 0.1 * float(2**attempt)
    if delay > 1.0:
        return 1.0
    return delay


def _build_headers_for_batch(batch: list[dict[str, object]]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    traceparent = _first_string_from_batch(batch, "traceparent")
    if traceparent is not None:
        headers["traceparent"] = traceparent
    request_id = _first_top_level_string(batch, "request_id")
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


def _first_string_from_batch(batch: list[dict[str, object]], key: str) -> str | None:
    for event in batch:
        extra = event.get("extra")
        if not isinstance(extra, Mapping):
            continue
        value = extra.get(key)
        if isinstance(value, str) and value != "":
            return value
    return None


def _first_top_level_string(batch: list[dict[str, object]], key: str) -> str | None:
    for event in batch:
        value = event.get(key)
        if isinstance(value, str) and value != "":
            return value
    return None


def _suppress_instrumentation() -> AbstractContextManager[object | None]:
    try:
        from opentelemetry.instrumentation.utils import suppress_instrumentation
    except ImportError:
        return nullcontext()
    return suppress_instrumentation()
