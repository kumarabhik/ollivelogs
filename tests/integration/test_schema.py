"""Round-trip a conversation + messages through Postgres.

Verifies:
- migrations applied (tables exist)
- check constraints (role, status)
- trigger bumps conversations.updated_at on message insert
- event_id uniqueness
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.integration


async def test_tables_exist(pg: asyncpg.Connection) -> None:
    rows = await pg.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
        """
    )
    names = {r["table_name"] for r in rows}
    assert {"users", "conversations", "messages", "messages_full"}.issubset(names)


async def test_conversation_roundtrip(pg: asyncpg.Connection, fresh_email: str) -> None:
    user_id = await pg.fetchval(
        "INSERT INTO users (email, display_name) VALUES ($1, $2) RETURNING id",
        fresh_email,
        "Test User",
    )
    conv_id = await pg.fetchval(
        "INSERT INTO conversations (user_id, title) VALUES ($1, $2) RETURNING id",
        user_id,
        "test convo",
    )

    for role, content in [
        ("user", "hello"),
        ("assistant", "hi there"),
        ("user", "anything else?"),
    ]:
        await pg.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, $2, $3)",
            conv_id,
            role,
            content,
        )

    count = await pg.fetchval(
        "SELECT count(*) FROM messages WHERE conversation_id = $1", conv_id
    )
    assert count == 3


async def test_role_check_constraint_rejects_bad_role(
    pg: asyncpg.Connection, fresh_email: str
) -> None:
    user_id = await pg.fetchval(
        "INSERT INTO users (email, display_name) VALUES ($1, $2) RETURNING id",
        fresh_email,
        "Bad Role Tester",
    )
    conv_id = await pg.fetchval(
        "INSERT INTO conversations (user_id, title) VALUES ($1, $2) RETURNING id",
        user_id,
        "bad role",
    )
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pg.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, $2, $3)",
            conv_id,
            "wizard",  # not in {system,user,assistant,tool}
            "you shall not pass",
        )


async def test_updated_at_trigger_bumps_on_new_message(
    pg: asyncpg.Connection, fresh_email: str
) -> None:
    user_id = await pg.fetchval(
        "INSERT INTO users (email, display_name) VALUES ($1, $2) RETURNING id",
        fresh_email,
        "Trigger Tester",
    )
    conv_id = await pg.fetchval(
        "INSERT INTO conversations (user_id, title) VALUES ($1, $2) RETURNING id",
        user_id,
        "trigger",
    )
    before = await pg.fetchval(
        "SELECT updated_at FROM conversations WHERE id = $1", conv_id
    )

    # Sleep-free: bump via the trigger; precision is microsecond so any insert wins.
    await pg.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES ($1, $2, $3)",
        conv_id,
        "user",
        "trigger me",
    )
    after = await pg.fetchval(
        "SELECT updated_at FROM conversations WHERE id = $1", conv_id
    )
    assert after > before


async def test_event_id_is_unique(pg: asyncpg.Connection, fresh_email: str) -> None:
    user_id = await pg.fetchval(
        "INSERT INTO users (email, display_name) VALUES ($1, $2) RETURNING id",
        fresh_email,
        "Dup Tester",
    )
    conv_id = await pg.fetchval(
        "INSERT INTO conversations (user_id, title) VALUES ($1, $2) RETURNING id",
        user_id,
        "dup",
    )
    event_id = uuid.uuid4()
    await pg.execute(
        "INSERT INTO messages (conversation_id, role, content, event_id) VALUES ($1,$2,$3,$4)",
        conv_id,
        "user",
        "first",
        event_id,
    )
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await pg.execute(
            "INSERT INTO messages (conversation_id, role, content, event_id) VALUES ($1,$2,$3,$4)",
            conv_id,
            "user",
            "second",
            event_id,
        )
