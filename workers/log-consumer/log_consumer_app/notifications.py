from __future__ import annotations

import asyncio
import json
import logging
import smtplib
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC
from email.message import EmailMessage
from typing import Protocol, cast

import asyncpg
import httpx
from redis.asyncio import Redis

from log_consumer_app.schemas import RedactedEventRecord
from log_consumer_app.settings import Settings

log = logging.getLogger("log-consumer.notifications")


class SupportsSmtp(Protocol):
    def __enter__(self) -> SupportsSmtp: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...

    def starttls(self) -> object: ...

    def login(self, username: str, password: str) -> object: ...

    def send_message(self, message: EmailMessage) -> object: ...


SmtpFactory = Callable[[], AbstractContextManager[SupportsSmtp]]


class WorkerNotifier:
    def __init__(
        self,
        *,
        settings: Settings,
        postgres_pool: asyncpg.Pool,
        redis_client: Redis,
        http_client: httpx.AsyncClient | None = None,
        smtp_factory: SmtpFactory | None = None,
    ) -> None:
        self._settings = settings
        self._postgres_pool = postgres_pool
        self._redis_client = redis_client
        self._http_client = http_client or httpx.AsyncClient(timeout=5.0)
        self._owns_http_client = http_client is None
        self._smtp_factory = smtp_factory or self._default_smtp_factory

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def notify_dlq(
        self,
        *,
        stream_id: str,
        reason: str,
        payload: str,
    ) -> None:
        summary = self._dlq_summary(stream_id=stream_id, reason=reason, payload=payload)
        if self._settings.dlq_slack_webhook_url:
            await self._post_webhook(
                self._settings.dlq_slack_webhook_url,
                {"text": summary},
            )
        if self._settings.dlq_discord_webhook_url:
            await self._post_webhook(
                self._settings.dlq_discord_webhook_url,
                {"content": summary},
            )

    async def maybe_send_budget_alert(self, record: RedactedEventRecord) -> None:
        if (
            self._settings.budget_alert_threshold_usd <= 0
            or self._settings.smtp_host is None
            or record.event.user_id is None
            or record.event.cost_usd <= 0
        ):
            return

        budget_day = record.event.ts.astimezone(UTC).date().isoformat()
        spend_key = f"budget:daily:{budget_day}:{record.event.user_id}"
        total_spend = float(
            await self._redis_client.incrbyfloat(
                spend_key,
                record.event.cost_usd,
            )
        )
        await self._redis_client.expire(spend_key, 60 * 60 * 48)
        if total_spend < self._settings.budget_alert_threshold_usd:
            return

        alert_key = (
            f"budget:alert:{budget_day}:{record.event.user_id}:"
            f"{self._settings.budget_alert_threshold_usd:.4f}"
        )
        claimed = await self._redis_client.set(alert_key, "1", ex=60 * 60 * 48, nx=True)
        if not claimed:
            return

        try:
            email = await self._lookup_user_email(record)
            if email is None:
                return
            await asyncio.to_thread(
                self._send_budget_email,
                recipient=email,
                threshold=self._settings.budget_alert_threshold_usd,
                total_spend=total_spend,
                record=record,
            )
        except Exception:
            await self._redis_client.delete(alert_key)
            raise

    async def _post_webhook(self, url: str, payload: dict[str, object]) -> None:
        response = await self._http_client.post(url, json=payload)
        response.raise_for_status()

    async def _lookup_user_email(self, record: RedactedEventRecord) -> str | None:
        email = await self._postgres_pool.fetchval(
            "SELECT email FROM users WHERE id = $1",
            record.event.user_id,
        )
        if isinstance(email, str) and email != "":
            return email
        return None

    def _send_budget_email(
        self,
        *,
        recipient: str,
        threshold: float,
        total_spend: float,
        record: RedactedEventRecord,
    ) -> None:
        message = EmailMessage()
        message["From"] = self._settings.budget_alert_from_email
        message["To"] = recipient
        message["Subject"] = "OlliveLogs daily budget alert"
        message.set_content(
            "\n".join(
                [
                    "Your OlliveLogs daily spend threshold was exceeded.",
                    f"Threshold: ${threshold:.4f}",
                    f"Current total: ${total_spend:.4f}",
                    f"Provider: {record.event.provider}",
                    f"Model: {record.event.model}",
                    f"Request ID: {record.request_id}",
                ]
            )
        )
        with self._smtp_factory() as client:
            if self._settings.smtp_use_tls:
                client.starttls()
            if self._settings.smtp_username and self._settings.smtp_password:
                client.login(self._settings.smtp_username, self._settings.smtp_password)
            client.send_message(message)

    def _default_smtp_factory(self) -> AbstractContextManager[SupportsSmtp]:
        assert self._settings.smtp_host is not None
        return cast(
            AbstractContextManager[SupportsSmtp],
            smtplib.SMTP(
                self._settings.smtp_host,
                self._settings.smtp_port,
                timeout=10,
            ),
        )

    def _dlq_summary(self, *, stream_id: str, reason: str, payload: str) -> str:
        preview = payload
        try:
            parsed = json.loads(payload)
            preview = json.dumps(parsed, separators=(",", ":"))[:240]
        except json.JSONDecodeError:
            preview = payload[:240]
        return (
            "OlliveLogs DLQ event\n"
            f"stream_id={stream_id}\n"
            f"reason={reason}\n"
            f"payload={preview}"
        )
