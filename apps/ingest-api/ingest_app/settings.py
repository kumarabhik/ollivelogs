from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings for the ingest API."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field(default="local")
    log_level: str = Field(default="INFO")

    database_url: str = Field(
        default="postgresql+asyncpg://ollive:ollive@postgres:5432/ollivelogs"
    )
    redis_url: str = Field(default="redis://redis:6379/0")

    event_bus_stream: str = Field(default="logs.raw")
    event_bus_idempotency_ttl_seconds: int = Field(default=60 * 60 * 24)
    event_bus_stream_maxlen: int = Field(default=100_000)

    ingest_max_batch_size: int = Field(default=500)
    ingest_metrics_path: str = Field(default="/metrics")


@lru_cache
def get_settings() -> Settings:
    return Settings()
