"""Shared pytest fixtures for OlliveLogs.

Integration tests expect docker compose services to be running locally:
    make dev
Then:
    pytest -m integration
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from redis.asyncio import Redis

ROOT = Path(__file__).resolve().parents[1]
CHAT_API_SRC = ROOT / "apps" / "chat-api"
INGEST_API_SRC = ROOT / "apps" / "ingest-api"
LOG_CONSUMER_SRC = ROOT / "workers" / "log-consumer"
OLLIVELOGS_PY_SRC = ROOT / "packages" / "ollivelogs-py"
if str(CHAT_API_SRC) not in sys.path:
    sys.path.insert(0, str(CHAT_API_SRC))
if str(INGEST_API_SRC) not in sys.path:
    sys.path.insert(0, str(INGEST_API_SRC))
if str(LOG_CONSUMER_SRC) not in sys.path:
    sys.path.insert(0, str(LOG_CONSUMER_SRC))
if str(OLLIVELOGS_PY_SRC) not in sys.path:
    sys.path.insert(0, str(OLLIVELOGS_PY_SRC))


def _dsn() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://ollive:ollive@localhost:5432/ollivelogs",
    ).replace("+asyncpg", "")


@pytest.fixture
async def pg() -> AsyncIterator[asyncpg.Connection]:
    """A raw asyncpg connection scoped to a single test."""
    conn = await asyncpg.connect(_dsn())
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6380/0"),
        decode_responses=True,
    )
    try:
        await client.flushdb()
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.fixture
def clickhouse_client() -> Iterator[object]:
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        database=os.getenv("CLICKHOUSE_DB", "ollivelogs"),
    )
    client.command(
        """
        CREATE TABLE IF NOT EXISTS ollivelogs.eval_runs
        (
            run_id UUID,
            ts DateTime64(3, 'UTC'),
            conversation_id UUID,
            turn_index UInt16,
            source_provider LowCardinality(String),
            source_model LowCardinality(String),
            against_provider LowCardinality(String),
            against_model LowCardinality(String),
            status LowCardinality(String),
            latency_ms UInt32,
            prompt_tokens UInt32 DEFAULT 0,
            completion_tokens UInt32 DEFAULT 0,
            total_tokens UInt32 DEFAULT 0,
            cost_usd Float64 DEFAULT 0,
            token_similarity Float64 DEFAULT 0,
            semantic_similarity Float64 DEFAULT 0,
            prompt_preview String,
            baseline_preview String,
            candidate_preview String,
            request_id String,
            extra String
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(ts)
        ORDER BY (ts, conversation_id, run_id, turn_index)
        TTL toDateTime(ts) + INTERVAL 90 DAY DELETE
        SETTINGS index_granularity = 8192
        """
    )
    client.command("TRUNCATE TABLE ollivelogs.inference_logs")
    client.command("TRUNCATE TABLE ollivelogs.inference_minute_agg")
    client.command("TRUNCATE TABLE ollivelogs.eval_runs")
    try:
        yield client
    finally:
        client.command("TRUNCATE TABLE ollivelogs.inference_logs")
        client.command("TRUNCATE TABLE ollivelogs.inference_minute_agg")
        client.command("TRUNCATE TABLE ollivelogs.eval_runs")
        client.close()


@pytest.fixture
def fresh_email() -> str:
    """A unique email per test, so seeded data doesn't collide."""
    return f"test-{uuid.uuid4().hex[:8]}@example.com"
