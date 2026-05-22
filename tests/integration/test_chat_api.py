from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from app.main import create_app
from app.settings import get_settings
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


@pytest.fixture
def api_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., Any]:
    @asynccontextmanager
    async def _factory(overrides: dict[str, str] | None = None) -> AsyncIterator[AsyncClient]:
        env = {
            "APP_ENV": "local",
            "DATABASE_URL": "postgresql+asyncpg://ollive:ollive@localhost:5432/ollivelogs",
            "REDIS_URL": "redis://localhost:6380/0",
            "STREAM_CHUNK_DELAY_MS": "10",
        }
        env.update(overrides or {})
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        get_settings.cache_clear()
        app = create_app()

        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                yield client

        get_settings.cache_clear()

    return _factory


@pytest.fixture
async def api_client(
    api_client_factory: Callable[..., Any],
) -> AsyncIterator[AsyncClient]:
    async with api_client_factory() as client:
        yield client


@pytest.mark.asyncio
async def test_create_list_get_and_archive_conversation_sets_session_cookie(
    api_client: AsyncClient,
) -> None:
    create_response = await api_client.post(
        "/v1/conversations",
        json={"title": "Roadmap session"},
    )
    assert create_response.status_code == 200
    assert "ollive_session=" in create_response.headers.get("set-cookie", "")

    conversation = create_response.json()
    conversation_id = conversation["id"]
    assert conversation["user_id"] is not None

    list_response = await api_client.get("/v1/conversations")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["total"] >= 1
    assert any(item["id"] == conversation_id for item in listed["items"])

    detail_response = await api_client.get(f"/v1/conversations/{conversation_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["messages"] == []

    delete_response = await api_client.delete(f"/v1/conversations/{conversation_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "archived"

    not_found_response = await api_client.get(f"/v1/conversations/{conversation_id}")
    assert not_found_response.status_code == 404


@pytest.mark.asyncio
async def test_anonymous_sessions_cannot_access_each_others_conversations(
    api_client_factory: Callable[..., Any],
) -> None:
    async with api_client_factory() as first_client:
        create_response = await first_client.post("/v1/conversations", json={"title": "Private"})
        assert create_response.status_code == 200
        conversation_id = create_response.json()["id"]

    async with api_client_factory() as second_client:
        detail_response = await second_client.get(f"/v1/conversations/{conversation_id}")
        assert detail_response.status_code == 404


@pytest.mark.asyncio
async def test_non_streaming_message_persists_and_counts_context_turns(
    api_client: AsyncClient,
) -> None:
    create_response = await api_client.post(
        "/v1/conversations",
        json={"title": "Context test"},
    )
    conversation_id = create_response.json()["id"]

    first_response = await api_client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "Hello there", "stream": False},
    )
    assert first_response.status_code == 200
    first_payload = first_response.json()
    assert first_payload["context_turns"] == 1
    assert first_payload["assistant_message"]["status"] == "ok"

    second_response = await api_client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "Can you use the prior turn?", "stream": False},
    )
    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["context_turns"] == 2
    assert "Context turns: 2" in second_payload["assistant_message"]["content"]

    detail_response = await api_client.get(f"/v1/conversations/{conversation_id}")
    messages = detail_response.json()["messages"]
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_streaming_cancel_persists_partial_assistant_message(
    api_client: AsyncClient,
    pg: asyncpg.Connection,
) -> None:
    create_response = await api_client.post(
        "/v1/conversations",
        json={"title": "Streaming test"},
    )
    conversation_id = create_response.json()["id"]

    async def trigger_cancel() -> None:
        await asyncio.sleep(0.03)
        cancel_response = await api_client.post(f"/v1/conversations/{conversation_id}/cancel")
        assert cancel_response.status_code == 200

    cancel_task = asyncio.create_task(trigger_cancel())

    async with api_client.stream(
        "POST",
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "Please stream a longer response for cancellation.", "stream": True},
    ) as response:
        assert response.status_code == 200
        last_event: str | None = None
        async for line in response.aiter_lines():
            if not line:
                continue
            if line.startswith("event: "):
                last_event = line.removeprefix("event: ")
            elif line.startswith("data: ") and last_event == "cancelled":
                cancelled_payload = json.loads(line.removeprefix("data: "))
                assert cancelled_payload["reason"] == "cancelled"
                break
    await cancel_task

    messages = await pg.fetch(
        """
        SELECT role, status, content
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at ASC, id ASC
        """,
        UUID(conversation_id),
    )
    assert [row["role"] for row in messages] == ["user", "assistant"]
    assert messages[-1]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_rate_limiting_returns_429_for_token_budget(
    api_client_factory: Callable[..., Any],
) -> None:
    async with api_client_factory(
        {"RATE_LIMIT_PER_MIN": "10", "RATE_LIMIT_TOKENS_PER_MIN": "3"}
    ) as limited_client:
        create_response = await limited_client.post(
            "/v1/conversations",
            json={"title": "Limited"},
        )
        assert create_response.status_code == 200
        conversation_id = create_response.json()["id"]

        first_response = await limited_client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"content": "one two", "stream": False},
        )
        assert first_response.status_code == 200

        second_response = await limited_client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"content": "three four", "stream": False},
        )
        assert second_response.status_code == 429


@pytest.mark.asyncio
async def test_send_message_to_missing_conversation_returns_not_found(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        "/v1/conversations/00000000-0000-0000-0000-000000000000/messages",
        json={"content": "Nope", "stream": False},
    )
    assert response.status_code == 404
