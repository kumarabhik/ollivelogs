from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import asyncpg
import clickhouse_connect
from clickhouse_connect.driver.client import Client
from fastapi import FastAPI, Request
from redis.asyncio import Redis

from log_consumer_app.settings import Settings, get_settings


async def create_postgres_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.database_url.replace("+asyncpg", ""),
        min_size=1,
        max_size=10,
        command_timeout=30,
    )


def create_clickhouse_client(settings: Settings) -> Client:
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    postgres_pool = await create_postgres_pool(settings)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    clickhouse_client = create_clickhouse_client(settings)

    await postgres_pool.fetchval("SELECT 1")
    await ping_redis(redis_client)
    await ping_clickhouse(clickhouse_client)

    app.state.settings = settings
    app.state.postgres_pool = postgres_pool
    app.state.redis = redis_client
    app.state.clickhouse = clickhouse_client

    try:
        yield
    finally:
        await redis_client.aclose()
        await postgres_pool.close()
        await asyncio.to_thread(clickhouse_client.close)


def get_settings_from_request(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_postgres_pool(request: Request) -> asyncpg.Pool:
    return cast(asyncpg.Pool, request.app.state.postgres_pool)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_clickhouse_client(request: Request) -> Client:
    return cast(Client, request.app.state.clickhouse)


async def ping_redis(redis_client: Redis) -> None:
    ping_result = redis_client.ping()
    if isinstance(ping_result, bool):
        return
    await ping_result


async def ping_clickhouse(clickhouse_client: Client) -> None:
    await asyncio.to_thread(clickhouse_client.query, "SELECT 1")
