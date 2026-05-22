from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import cast
from uuid import UUID

import asyncpg
import httpx
import pytest
from log_consumer_app.notifications import WorkerNotifier
from log_consumer_app.schemas import InferenceLogEvent, RedactedEventRecord
from log_consumer_app.settings import Settings
from redis.asyncio import Redis

from tests.helpers import build_event

pytestmark = pytest.mark.unit


@dataclass(slots=True)
class FakeRedis:
    float_values: dict[str, float] = field(default_factory=dict)
    string_values: dict[str, str] = field(default_factory=dict)
    expiries: dict[str, int] = field(default_factory=dict)

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.float_values[key] = self.float_values.get(key, 0.0) + amount
        return self.float_values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expiries[key] = seconds
        return True

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool | None:
        if nx and key in self.string_values:
            return None
        self.string_values[key] = value
        self.expiries[key] = ex
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.string_values
        self.string_values.pop(key, None)
        return 1 if existed else 0


@dataclass(slots=True)
class FakePostgresPool:
    email: str | None
    fetches: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def fetchval(self, query: str, *args: object) -> str | None:
        self.fetches.append((query, args))
        return self.email


@dataclass(slots=True)
class FakeSmtpClient:
    messages: list[EmailMessage]
    started_tls: bool = False
    logins: list[tuple[str, str]] = field(default_factory=list)

    def __enter__(self) -> FakeSmtpClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type
        del exc
        del tb

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logins.append((username, password))

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


def _record(*, user_id: str | None = None, cost_usd: float = 0.5) -> RedactedEventRecord:
    payload = build_event(request_preview="hello", response_preview="world")
    payload["user_id"] = user_id
    payload["cost_usd"] = cost_usd
    event = InferenceLogEvent.model_validate(payload)
    return RedactedEventRecord(
        event=event,
        input_preview="hello",
        output_preview="world",
        request_id="req_budget",
        raw_content="world",
    )


@pytest.mark.asyncio
async def test_notify_dlq_posts_to_slack_and_discord() -> None:
    captured_requests: list[tuple[str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(
            (
                str(request.url),
                json.loads(request.content.decode("utf-8")),
            )
        )
        return httpx.Response(200, json={"ok": True})

    notifier = WorkerNotifier(
        settings=Settings(
            dlq_slack_webhook_url="https://hooks.slack.test/1",
            dlq_discord_webhook_url="https://discord.test/2",
        ),
        postgres_pool=cast(asyncpg.Pool, FakePostgresPool(email=None)),
        redis_client=cast(Redis, FakeRedis()),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    await notifier.notify_dlq(
        stream_id="1777-0",
        reason="ForeignKeyViolationError:missing conversation",
        payload='{"conversation_id":"123"}',
    )
    await notifier.aclose()

    assert [request[0] for request in captured_requests] == [
        "https://hooks.slack.test/1",
        "https://discord.test/2",
    ]
    assert cast(str, captured_requests[0][1]["text"]).startswith("OlliveLogs DLQ event")
    assert cast(str, captured_requests[1][1]["content"]).startswith("OlliveLogs DLQ event")


@pytest.mark.asyncio
async def test_budget_alert_emails_once_after_threshold_crossing() -> None:
    sent_messages: list[EmailMessage] = []
    smtp_client = FakeSmtpClient(messages=sent_messages)
    redis_client = FakeRedis()
    postgres_pool = FakePostgresPool(email="alerts@example.com")
    notifier = WorkerNotifier(
        settings=Settings(
            budget_alert_threshold_usd=1.0,
            smtp_host="smtp.test",
            smtp_port=2525,
            smtp_username="user",
            smtp_password="pass",
            budget_alert_from_email="alerts@ollivelogs.local",
        ),
        postgres_pool=cast(asyncpg.Pool, postgres_pool),
        redis_client=cast(Redis, redis_client),
        smtp_factory=lambda: _smtp_factory(smtp_client),
    )
    user_id = str(UUID("11111111-1111-1111-1111-111111111111"))

    await notifier.maybe_send_budget_alert(_record(user_id=user_id, cost_usd=0.6))
    await notifier.maybe_send_budget_alert(_record(user_id=user_id, cost_usd=0.5))
    await notifier.maybe_send_budget_alert(_record(user_id=user_id, cost_usd=0.4))
    await notifier.aclose()

    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == "alerts@example.com"
    assert "Current total: $1.1000" in sent_messages[0].get_content()
    assert smtp_client.started_tls is True
    assert smtp_client.logins == [("user", "pass")]
    assert postgres_pool.fetches


@contextmanager
def _smtp_factory(client: FakeSmtpClient) -> Iterator[FakeSmtpClient]:
    yield client
