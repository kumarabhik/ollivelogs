from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import asyncpg
import httpx
from fastapi import FastAPI, Request
from redis.asyncio import Redis

from app.inference_logging import InferenceLogger, build_inference_logger
from app.pricing import PricingCatalog
from app.providers import ProviderRegistry, build_provider_registry
from app.settings import Settings, get_settings


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
    http_client = httpx.AsyncClient(
        timeout=settings.provider_timeout_seconds,
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=120.0,
        ),
    )
    pricing = PricingCatalog.from_repo_file()
    provider_registry = build_provider_registry(settings, http_client, pricing)
    inference_logger = build_inference_logger(settings)

    await postgres_pool.fetchval("SELECT 1")
    await ping_redis(redis_client)

    app.state.settings = settings
    app.state.postgres_pool = postgres_pool
    app.state.redis = redis_client
    app.state.provider_registry = provider_registry
    app.state.inference_logger = inference_logger

    try:
        yield
    finally:
        if inference_logger is not None:
            inference_logger.close(timeout_seconds=settings.ollive_sdk_flush_timeout_seconds)
        await http_client.aclose()
        await redis_client.aclose()
        await postgres_pool.close()


def get_settings_from_request(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_postgres_pool(request: Request) -> asyncpg.Pool:
    return cast(asyncpg.Pool, request.app.state.postgres_pool)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_provider_registry(request: Request) -> ProviderRegistry:
    return cast(ProviderRegistry, request.app.state.provider_registry)


def get_inference_logger(request: Request) -> InferenceLogger | None:
    return cast(InferenceLogger | None, request.app.state.inference_logger)


async def ping_redis(redis_client: Redis) -> None:
    ping_result = redis_client.ping()
    if isinstance(ping_result, bool):
        return
    await ping_result
