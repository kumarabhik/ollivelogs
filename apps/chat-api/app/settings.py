from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised env-driven config. Read via `get_settings()`."""

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

    default_provider: str = Field(default="huggingface")
    default_model: str = Field(default="Qwen/Qwen2.5-72B-Instruct")
    provider_failover_enabled: bool = Field(default=True)
    provider_failover_map: str = Field(
        default="openai=anthropic;anthropic=openai;gemini=openai;deepseek=openai;grok=openai;huggingface=openai"
    )
    provider_timeout_seconds: float = Field(default=30.0)
    provider_max_tokens: int = Field(default=1024)
    openai_default_model: str = Field(default="gpt-4.1-mini")
    anthropic_default_model: str = Field(default="claude-sonnet-4-20250514")
    google_default_model: str = Field(default="gemini-2.5-flash")
    deepseek_default_model: str = Field(default="deepseek-chat")
    xai_default_model: str = Field(default="grok-beta")
    hf_default_model: str = Field(default="Qwen/Qwen2.5-72B-Instruct")

    session_secret: str = Field(default="dev-secret")
    allow_anonymous: bool = Field(default=True)
    session_cookie_name: str = Field(default="ollive_session")
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 30)
    session_cookie_secure: bool = Field(default=False)

    rate_limit_per_min: int = Field(default=30)
    rate_limit_tokens_per_min: int = Field(default=2000)
    chat_context_turns: int = Field(default=20)
    stream_chunk_delay_ms: int = Field(default=20)
    cancel_ttl_seconds: int = Field(default=300)

    openai_api_key: str | None = Field(default=None)
    openai_base_url: str = Field(default="https://api.openai.com/v1")

    anthropic_api_key: str | None = Field(default=None)
    anthropic_base_url: str = Field(default="https://api.anthropic.com")
    anthropic_version: str = Field(default="2023-06-01")

    google_api_key: str | None = Field(default=None)
    google_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta"
    )

    deepseek_api_key: str | None = Field(default=None)
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1")

    xai_api_key: str | None = Field(default=None)
    xai_base_url: str = Field(default="https://api.x.ai/v1")

    hf_token: str | None = Field(default=None)
    hf_base_url: str = Field(default="https://router.huggingface.co/v1")

    ollive_ingest_url: str | None = Field(default=None)
    ollive_sdk_batch_size: int = Field(default=1)
    ollive_sdk_flush_ms: int = Field(default=25)
    ollive_sdk_queue_max: int = Field(default=256)
    ollive_sdk_flush_timeout_seconds: float = Field(default=2.0)

    def provider_default_model(self, provider_name: str) -> str:
        defaults = {
            "openai": self.openai_default_model,
            "anthropic": self.anthropic_default_model,
            "gemini": self.google_default_model,
            "deepseek": self.deepseek_default_model,
            "grok": self.xai_default_model,
            "huggingface": self.hf_default_model,
            "demo": self.default_model,
        }
        return defaults.get(provider_name, self.default_model)

    def failover_targets(self, provider_name: str) -> list[str]:
        mapping: dict[str, list[str]] = {}
        for item in self.provider_failover_map.split(";"):
            item = item.strip()
            if item == "" or "=" not in item:
                continue
            key, raw_targets = item.split("=", 1)
            targets = [target.strip() for target in raw_targets.split(",") if target.strip() != ""]
            if key.strip() != "" and targets:
                mapping[key.strip()] = targets
        return mapping.get(provider_name, [])


@lru_cache
def get_settings() -> Settings:
    return Settings()
