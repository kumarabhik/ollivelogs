from __future__ import annotations

import socket
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings for the log consumer."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field(default="local")
    log_level: str = Field(default="INFO")

    database_url: str = Field(
        default="postgresql+asyncpg://ollive:ollive@postgres:5432/ollivelogs"
    )
    redis_url: str = Field(default="redis://redis:6379/0")
    clickhouse_host: str = Field(default="clickhouse")
    clickhouse_port: int = Field(default=8123)
    clickhouse_db: str = Field(default="ollivelogs")
    clickhouse_user: str = Field(default="default")
    clickhouse_password: str = Field(default="")

    event_bus_stream: str = Field(default="logs.raw")
    event_bus_dlq: str = Field(default="logs.dlq")
    event_bus_consumer_group: str = Field(default="cg-store")
    event_bus_consumer_name: str = Field(default_factory=socket.gethostname)

    worker_batch_size: int = Field(default=100, ge=1, le=500)
    worker_block_ms: int = Field(default=200, ge=1, le=5_000)
    worker_metrics_path: str = Field(default="/metrics")

    pii_redaction_enabled: bool = Field(default=True)
    store_raw: bool = Field(default=False)
    raw_encryption_key: str = Field(default="change-me-32-bytes")
    dlq_slack_webhook_url: str | None = Field(default=None)
    dlq_discord_webhook_url: str | None = Field(default=None)
    budget_alert_threshold_usd: float = Field(default=0.0, ge=0.0)
    smtp_host: str | None = Field(default=None)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)
    smtp_use_tls: bool = Field(default=True)
    budget_alert_from_email: str = Field(default="alerts@ollivelogs.local")


@lru_cache
def get_settings() -> Settings:
    return Settings()
