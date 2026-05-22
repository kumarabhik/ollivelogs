from __future__ import annotations

import asyncpg
from fastapi import Depends, Request
from redis.asyncio import Redis

from ingest_app.db import get_postgres_pool, get_redis, get_settings_from_request
from ingest_app.settings import Settings


def settings_dep(request: Request) -> Settings:
    return get_settings_from_request(request)


def postgres_pool_dep(request: Request) -> asyncpg.Pool:
    return get_postgres_pool(request)


def redis_dep(request: Request) -> Redis:
    return get_redis(request)


SettingsDep = Depends(settings_dep)
PostgresPoolDep = Depends(postgres_pool_dep)
RedisDep = Depends(redis_dep)
