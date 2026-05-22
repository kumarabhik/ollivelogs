from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import asyncpg
from fastapi import FastAPI, Request
from redis.asyncio import Redis

from ingest_app.settings import Settings, get_settings


async def create_postgres_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.database_url.replace("+asyncpg", ""),
        min_size=1,
        max_size=10,
        command_timeout=30,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    postgres_pool = await create_postgres_pool(settings)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)

    await postgres_pool.fetchval("SELECT 1")
    await ping_redis(redis_client)

    app.state.settings = settings
    app.state.postgres_pool = postgres_pool
    app.state.redis = redis_client

    try:
        yield
    finally:
        await redis_client.aclose()
        await postgres_pool.close()


def get_settings_from_request(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_postgres_pool(request: Request) -> asyncpg.Pool:
    return cast(asyncpg.Pool, request.app.state.postgres_pool)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


async def ping_redis(redis_client: Redis) -> None:
    ping_result = redis_client.ping()
    if isinstance(ping_result, bool):
        return
    await ping_result
