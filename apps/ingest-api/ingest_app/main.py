from __future__ import annotations

import gzip
import json
import logging
import time
from collections.abc import Awaitable, Callable

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from redis.asyncio import Redis

from ingest_app.db import lifespan, ping_redis
from ingest_app.deps import PostgresPoolDep, RedisDep, SettingsDep
from ingest_app.metrics import (
    INGEST_BATCH_SIZE,
    INGEST_EVENTS_TOTAL,
    INGEST_REQUEST_DURATION_SECONDS,
    metrics_response,
)
from ingest_app.observability import install_observability
from ingest_app.request_context import bind_request_id, resolve_request_id
from ingest_app.schemas import IngestResponse, parse_events
from ingest_app.service import publish_events
from ingest_app.settings import Settings, get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)
log = logging.getLogger("ingest-api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="OlliveLogs ingest-api",
        version="0.1.0",
        description="Validated log ingestion into Redis Streams.",
        lifespan=lifespan,
    )

    install_observability(app)

    @app.middleware("http")
    async def request_context_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = resolve_request_id(request.headers.get("x-request-id"))
        started_at = time.perf_counter()
        with bind_request_id(request_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "http_request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        return response

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "service": "ollivelogs-ingest-api",
            "version": "0.1.0",
            "docs": "/docs",
        }

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "ingest-api"})

    @app.get("/readyz", tags=["meta"])
    async def readyz(
        postgres_pool: asyncpg.Pool = PostgresPoolDep,
        redis_client: Redis = RedisDep,
    ) -> JSONResponse:
        await postgres_pool.fetchval("SELECT 1")
        await ping_redis(redis_client)
        return JSONResponse({"status": "ready"})

    @app.get(settings.ingest_metrics_path, tags=["meta"])
    async def metrics() -> Response:
        return metrics_response()

    @app.post("/v1/logs", response_model=IngestResponse, tags=["logs"])
    async def ingest_logs(
        request: Request,
        redis_client: Redis = RedisDep,
        app_settings: Settings = SettingsDep,
    ) -> IngestResponse:
        started_at = time.perf_counter()
        try:
            payload = await _decode_json_body(request)
            events = parse_events(payload)
        except ValueError as exc:
            INGEST_EVENTS_TOTAL.labels(status="dropped").inc()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValidationError as exc:
            INGEST_EVENTS_TOTAL.labels(status="dropped").inc()
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        finally:
            pass

        if not events:
            INGEST_EVENTS_TOTAL.labels(status="dropped").inc()
            raise HTTPException(status_code=422, detail="At least one event is required.")
        if len(events) > app_settings.ingest_max_batch_size:
            INGEST_EVENTS_TOTAL.labels(status="dropped").inc()
            raise HTTPException(
                status_code=413,
                detail=f"Batch size {len(events)} exceeds limit {app_settings.ingest_max_batch_size}.",
            )

        publish_result = await publish_events(redis_client, app_settings, events)
        INGEST_BATCH_SIZE.observe(len(events))
        if publish_result.accepted:
            INGEST_EVENTS_TOTAL.labels(status="ok").inc(publish_result.accepted)
        if publish_result.deduped:
            INGEST_EVENTS_TOTAL.labels(status="dedup").inc(publish_result.deduped)
        INGEST_REQUEST_DURATION_SECONDS.observe(time.perf_counter() - started_at)

        if publish_result.accepted == 0 and publish_result.deduped > 0:
            log.info("ingest_deduped_batch", extra={"events": len(events)})

        return IngestResponse(
            accepted=publish_result.accepted,
            deduped=publish_result.deduped,
            event_ids=[event.event_id for event in events],
            stream_ids=publish_result.stream_ids,
        )

    return app


app = create_app()


async def _decode_json_body(request: Request) -> object:
    body = await request.body()
    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except OSError as exc:
            raise ValueError("Request body declared gzip encoding but could not be decompressed.") from exc
    if not body:
        raise ValueError("Request body must not be empty.")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
