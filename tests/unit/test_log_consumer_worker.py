from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from log_consumer_app.settings import Settings
from log_consumer_app.worker import LogConsumerWorker, StreamEntry

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_stop_drains_entries_already_pulled_into_the_current_batch() -> None:
    worker = LogConsumerWorker(
        settings=Settings(worker_block_ms=1),
        postgres_pool=cast(Any, object()),
        redis_client=cast(Any, object()),
        clickhouse_client=cast(Any, object()),
    )
    batch_started = asyncio.Event()
    first_entry_released = asyncio.Event()
    delivered = False
    processed: list[str] = []

    async def fake_read_batch() -> list[StreamEntry]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return [
                StreamEntry(stream_id="1-0", payload="first"),
                StreamEntry(stream_id="2-0", payload="second"),
            ]
        await asyncio.sleep(0.01)
        return []

    async def fake_process_entry(entry: StreamEntry) -> None:
        processed.append(entry.stream_id)
        if entry.stream_id == "1-0":
            batch_started.set()
            await first_entry_released.wait()

    worker._read_batch = fake_read_batch  # type: ignore[method-assign]
    worker._process_entry = fake_process_entry  # type: ignore[method-assign]
    worker._task = asyncio.create_task(worker.consume_forever())

    await batch_started.wait()
    stop_task = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    first_entry_released.set()
    await stop_task

    assert processed == ["1-0", "2-0"]
