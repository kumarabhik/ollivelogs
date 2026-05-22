from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import asyncpg
from clickhouse_connect.driver.client import Client
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis

from log_consumer_app.db import (
    lifespan as base_lifespan,
)
from log_consumer_app.db import (
    ping_clickhouse,
    ping_redis,
)
from log_consumer_app.metrics import metrics_response
from log_consumer_app.observability import install_observability
from log_consumer_app.settings import Settings, get_settings
from log_consumer_app.worker import LogConsumerWorker

settings = get_settings()
logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with base_lifespan(app):
        worker = LogConsumerWorker(
            settings=get_settings_from_app(app),
            postgres_pool=get_postgres_pool_from_app(app),
            redis_client=get_redis_from_app(app),
            clickhouse_client=get_clickhouse_client_from_app(app),
        )
        await worker.start()
        app.state.worker = worker
        try:
            yield
        finally:
            await worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="OlliveLogs log-consumer",
        version="0.1.0",
        description="Redis Streams worker that redacts and persists inference logs.",
        lifespan=lifespan,
    )

    install_observability(app)

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "service": "ollivelogs-log-consumer",
            "version": "0.1.0",
            "docs": "/docs",
        }

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "log-consumer"})

    @app.get("/readyz", tags=["meta"])
    async def readyz() -> JSONResponse:
        postgres_pool = get_postgres_pool_from_app(app)
        redis_client = get_redis_from_app(app)
        clickhouse_client = get_clickhouse_client_from_app(app)
        await postgres_pool.fetchval("SELECT 1")
        await ping_redis(redis_client)
        await ping_clickhouse(clickhouse_client)
        return JSONResponse({"status": "ready"})

    @app.get(settings.worker_metrics_path, tags=["meta"])
    async def metrics() -> Response:
        return metrics_response()

    return app


def get_settings_from_app(app: FastAPI) -> Settings:
    return cast(Settings, app.state.settings)


def get_postgres_pool_from_app(app: FastAPI) -> asyncpg.Pool:
    return cast(asyncpg.Pool, app.state.postgres_pool)


def get_redis_from_app(app: FastAPI) -> Redis:
    return cast(Redis, app.state.redis)


def get_clickhouse_client_from_app(app: FastAPI) -> Client:
    return cast(Client, app.state.clickhouse)


app = create_app()
