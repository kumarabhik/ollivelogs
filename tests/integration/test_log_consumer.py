from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from ingest_app.main import create_app as create_ingest_app
from ingest_app.settings import get_settings as get_ingest_settings
from log_consumer_app.main import create_app as create_worker_app
from log_consumer_app.settings import get_settings as get_worker_settings

from tests.helpers import build_event

pytestmark = pytest.mark.integration


@pytest.fixture
def ingest_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., Any]:
    @asynccontextmanager
    async def _factory(overrides: dict[str, str] | None = None) -> AsyncIterator[AsyncClient]:
        env = {
            "APP_ENV": "local",
            "DATABASE_URL": "postgresql+asyncpg://ollive:ollive@localhost:5432/ollivelogs",
            "REDIS_URL": "redis://localhost:6380/0",
        }
        env.update(overrides or {})
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        get_ingest_settings.cache_clear()
        app = create_ingest_app()

        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                yield client

        get_ingest_settings.cache_clear()

    return _factory


@pytest.fixture
def worker_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., Any]:
    @asynccontextmanager
    async def _factory(overrides: dict[str, str] | None = None) -> AsyncIterator[AsyncClient]:
        env = {
            "APP_ENV": "local",
            "DATABASE_URL": "postgresql+asyncpg://ollive:ollive@localhost:5432/ollivelogs",
            "REDIS_URL": "redis://localhost:6380/0",
            "CLICKHOUSE_HOST": "localhost",
            "CLICKHOUSE_PORT": "8123",
            "WORKER_BLOCK_MS": "50",
        }
        env.update(overrides or {})
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        get_worker_settings.cache_clear()
        app = create_worker_app()

        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                yield client

        get_worker_settings.cache_clear()

    return _factory


async def wait_for(
    predicate: Callable[[], Any],
    *,
    timeout_seconds: float = 5.0,
    interval_seconds: float = 0.05,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        value = await predicate()
        if value:
            return value
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for condition.")
        await asyncio.sleep(interval_seconds)


@pytest.mark.asyncio
async def test_end_to_end_pipeline_batches_redacts_and_persists(
    ingest_client_factory: Callable[..., Any],
    worker_client_factory: Callable[..., Any],
    pg: asyncpg.Connection,
    redis_client: Any,
    clickhouse_client: Any,
) -> None:
    stream_name = f"logs.raw.{uuid.uuid4().hex}"
    dlq_name = f"logs.dlq.{uuid.uuid4().hex}"
    group_name = f"cg-store-{uuid.uuid4().hex[:8]}"
    conversation_id = await pg.fetchval(
        "INSERT INTO conversations (user_id, title) VALUES (NULL, $1) RETURNING id",
        "worker pipeline",
    )

    batch = [
        build_event(
            conversation_id=str(conversation_id),
            request_preview="Reach me at +91 9876543210 with Aadhaar 1234 1234 1234.",
            response_preview="Your PAN ABCDE1234F and IFSC HDFC0001234 are now masked.",
        ),
        build_event(
            conversation_id=str(conversation_id),
            request_preview="Second request with Aadhaar 9999 8888 7777",
            response_preview="Second response IFSC SBIN0004321",
        ),
        build_event(
            conversation_id=str(conversation_id),
            request_preview="Third request +91 9123456789",
            response_preview="Third response PAN PQRSX1234Z",
        ),
    ]

    async with worker_client_factory(
        {
            "EVENT_BUS_STREAM": stream_name,
            "EVENT_BUS_DLQ": dlq_name,
            "EVENT_BUS_CONSUMER_GROUP": group_name,
            "WORKER_BATCH_SIZE": "2",
        }
    ) as worker_client, ingest_client_factory(
        {
            "EVENT_BUS_STREAM": stream_name,
        }
    ) as ingest_client:
        response = await ingest_client.post("/v1/logs", json={"events": batch})
        assert response.status_code == 200
        assert response.json()["accepted"] == 3

        groups = await redis_client.xinfo_groups(stream_name)
        assert any(group["name"] == group_name for group in groups)

        async def messages_ready() -> list[asyncpg.Record]:
            rows = await pg.fetch(
                """
                    SELECT content, status, event_id
                    FROM messages
                    WHERE conversation_id = $1
                    ORDER BY created_at ASC, id ASC
                    """,
                conversation_id,
            )
            return rows if len(rows) == 3 else []

        rows = await wait_for(messages_ready)
        assert len(rows) == 3
        assert all(row["status"] == "ok" for row in rows)
        assert "<PAN>" in rows[0]["content"] or "<IFSC>" in rows[0]["content"]
        assert "ABCDE1234F" not in rows[0]["content"]
        assert "HDFC0001234" not in rows[0]["content"]

        async def clickhouse_ready() -> list[Any]:
            def _query() -> list[Any]:
                query = clickhouse_client.query(
                    "SELECT input_preview, output_preview FROM ollivelogs.inference_logs ORDER BY ts"
                )
                return list(query.result_rows)

            rows = await asyncio.to_thread(_query)
            return rows if len(rows) == 3 else []

        result_rows = await wait_for(clickhouse_ready)
        assert len(result_rows) == 3
        assert "<AADHAAR>" in result_rows[0][0] or "<INDIA_PHONE>" in result_rows[0][0]
        assert "1234 1234 1234" not in result_rows[0][0]

        metrics = await worker_client.get("/metrics")
        assert metrics.status_code == 200
        assert "worker_batch_size_bucket" in metrics.text

    dlq_entries = await redis_client.xrange(dlq_name)
    assert dlq_entries == []


@pytest.mark.asyncio
async def test_worker_sends_failures_to_dlq(
    ingest_client_factory: Callable[..., Any],
    worker_client_factory: Callable[..., Any],
    redis_client: Any,
    clickhouse_client: Any,
) -> None:
    stream_name = f"logs.raw.{uuid.uuid4().hex}"
    dlq_name = f"logs.dlq.{uuid.uuid4().hex}"
    group_name = f"cg-store-{uuid.uuid4().hex[:8]}"
    missing_conversation_id = str(uuid.uuid4())
    event = build_event(conversation_id=missing_conversation_id)

    async with worker_client_factory(
        {
            "EVENT_BUS_STREAM": stream_name,
            "EVENT_BUS_DLQ": dlq_name,
            "EVENT_BUS_CONSUMER_GROUP": group_name,
        }
    ), ingest_client_factory({"EVENT_BUS_STREAM": stream_name}) as ingest_client:
        response = await ingest_client.post("/v1/logs", json=event)
        assert response.status_code == 200
        assert response.json()["accepted"] == 1

        async def dlq_ready() -> list[Any]:
            entries = await redis_client.xrange(dlq_name)
            return entries if entries else []

        entries = await wait_for(dlq_ready)
        assert len(entries) == 1
        _, fields = entries[0]
        assert "error_reason" in fields
        assert "ForeignKeyViolationError" in fields["error_reason"]
        assert json.loads(fields["payload"])["conversation_id"] == missing_conversation_id

        def clickhouse_count() -> int:
            query = clickhouse_client.query(
                "SELECT count() FROM ollivelogs.inference_logs WHERE conversation_id = %(conversation)s",
                parameters={"conversation": missing_conversation_id},
            )
            return int(query.first_row[0])

        assert await asyncio.to_thread(clickhouse_count) == 0
