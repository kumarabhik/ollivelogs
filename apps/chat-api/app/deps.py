from __future__ import annotations

import asyncpg
from fastapi import Depends, Request
from redis.asyncio import Redis

from app.db import (
    get_inference_logger,
    get_postgres_pool,
    get_provider_registry,
    get_redis,
    get_settings_from_request,
)
from app.inference_logging import InferenceLogger
from app.providers import ProviderRegistry
from app.settings import Settings


def settings_dep(request: Request) -> Settings:
    return get_settings_from_request(request)


def postgres_pool_dep(request: Request) -> asyncpg.Pool:
    return get_postgres_pool(request)


def redis_dep(request: Request) -> Redis:
    return get_redis(request)


def provider_registry_dep(request: Request) -> ProviderRegistry:
    return get_provider_registry(request)


def inference_logger_dep(request: Request) -> InferenceLogger | None:
    return get_inference_logger(request)


SettingsDep = Depends(settings_dep)
PostgresPoolDep = Depends(postgres_pool_dep)
RedisDep = Depends(redis_dep)
ProviderRegistryDep = Depends(provider_registry_dep)
InferenceLoggerDep = Depends(inference_logger_dep)
