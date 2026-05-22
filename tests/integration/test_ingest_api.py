from __future__ import annotations

import gzip
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from ingest_app.main import create_app
from ingest_app.settings import get_settings

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

        get_settings.cache_clear()
        app = create_app()

        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                yield client

        get_settings.cache_clear()

    return _factory


@pytest.fixture
async def ingest_client(
    ingest_client_factory: Callable[..., Any],
) -> AsyncIterator[AsyncClient]:
    async with ingest_client_factory() as client:
        yield client


@pytest.mark.asyncio
async def test_ingest_single_event_publishes_to_stream(
    ingest_client_factory: Callable[..., Any],
    redis_client: Any,
) -> None:
    stream_name = f"logs.raw.{uuid.uuid4().hex}"
    async with ingest_client_factory({"EVENT_BUS_STREAM": stream_name}) as client:
        event = build_event()
        response = await client.post("/v1/logs", json=event)
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] == 1
        assert payload["deduped"] == 0
        assert payload["event_ids"] == [event["event_id"]]

    entries = await redis_client.xrange(stream_name)
    assert len(entries) == 1
    _, fields = entries[0]
    pushed_event = json.loads(fields["payload"])
    assert pushed_event["event_id"] == event["event_id"]
    assert pushed_event["provider"] == "openai"


@pytest.mark.asyncio
async def test_ingest_accepts_gzip_batch_and_deduplicates(
    ingest_client_factory: Callable[..., Any],
    redis_client: Any,
) -> None:
    stream_name = f"logs.raw.{uuid.uuid4().hex}"
    duplicate_id = str(uuid.uuid4())
    batch = [
        build_event(event_id=duplicate_id),
        build_event(event_id=duplicate_id),
    ]
    compressed = gzip.compress(json.dumps(batch).encode("utf-8"))

    async with ingest_client_factory({"EVENT_BUS_STREAM": stream_name}) as client:
        response = await client.post(
            "/v1/logs",
            content=compressed,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] == 1
        assert payload["deduped"] == 1

    entries = await redis_client.xrange(stream_name)
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_ingest_strict_validation_rejects_bad_payload_and_exposes_metrics(
    ingest_client: AsyncClient,
) -> None:
    bad_event = build_event()
    bad_event["usage"]["prompt_tokens"] = "12"

    response = await ingest_client.post("/v1/logs", json=bad_event)
    assert response.status_code == 422

    ready = await ingest_client.get("/readyz")
    assert ready.status_code == 200

    metrics = await ingest_client.get("/metrics")
    assert metrics.status_code == 200
    assert 'ingest_events_total{status="dropped"}' in metrics.text
