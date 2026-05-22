from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, cast
from uuid import UUID

import asyncpg

from app.providers import ContextMessage
from app.schemas import ConversationDetail, ConversationSummary, MessageResource


def _conversation_from_row(row: asyncpg.Record) -> ConversationSummary:
    return ConversationSummary.model_validate(dict(row))


def _message_from_row(row: asyncpg.Record) -> MessageResource:
    return MessageResource.model_validate(dict(row))


def _user_from_row(row: asyncpg.Record) -> dict[str, Any]:
    return dict(row)


async def ping_postgres(pool: asyncpg.Pool) -> None:
    await pool.fetchval("SELECT 1")


async def create_conversation(
    pool: asyncpg.Pool,
    user_id: UUID | None,
    title: str | None,
    model_default: str,
) -> ConversationSummary:
    row = await pool.fetchrow(
        """
        INSERT INTO conversations (user_id, title, model_default)
        VALUES ($1, $2, $3)
        RETURNING id, user_id, title, status, model_default, created_at, updated_at
        """,
        user_id,
        title,
        model_default,
    )
    return _conversation_from_row(cast(asyncpg.Record, row))


async def list_conversations(
    pool: asyncpg.Pool,
    user_id: UUID,
    limit: int,
    offset: int,
) -> tuple[int, list[ConversationSummary]]:
    total = cast(
        int,
        await pool.fetchval(
            """
            SELECT count(*)
            FROM conversations
            WHERE status <> 'archived'
              AND user_id = $1::uuid
            """,
            user_id,
        ),
    )
    rows = await pool.fetch(
        """
        SELECT id, user_id, title, status, model_default, created_at, updated_at
        FROM conversations
        WHERE status <> 'archived'
          AND user_id = $1::uuid
        ORDER BY updated_at DESC, created_at DESC
        LIMIT $2 OFFSET $3
        """,
        user_id,
        limit,
        offset,
    )
    return total, [_conversation_from_row(row) for row in rows]


async def get_conversation(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    user_id: UUID,
    *,
    include_archived: bool = False,
) -> ConversationSummary | None:
    query = """
        SELECT id, user_id, title, status, model_default, created_at, updated_at
        FROM conversations
        WHERE id = $1 AND user_id = $2
    """
    if not include_archived:
        query += " AND status <> 'archived'"

    row = await pool.fetchrow(query, conversation_id, user_id)
    if row is None:
        return None
    return _conversation_from_row(row)


async def get_messages(pool: asyncpg.Pool, conversation_id: UUID) -> list[MessageResource]:
    rows = await pool.fetch(
        """
        SELECT id, conversation_id, role, content, token_count, status, event_id, created_at
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at ASC, id ASC
        """,
        conversation_id,
    )
    return [_message_from_row(row) for row in rows]


async def get_conversation_detail(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    user_id: UUID,
) -> ConversationDetail | None:
    conversation = await get_conversation(pool, conversation_id, user_id)
    if conversation is None:
        return None

    messages = await get_messages(pool, conversation_id)
    return ConversationDetail(**conversation.model_dump(), messages=messages)


async def insert_message(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    role: str,
    content: str,
    *,
    status: str = "ok",
    token_count: int | None = None,
) -> MessageResource:
    row = await pool.fetchrow(
        """
        INSERT INTO messages (conversation_id, role, content, token_count, status)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, conversation_id, role, content, token_count, status, event_id, created_at
        """,
        conversation_id,
        role,
        content,
        token_count,
        status,
    )
    return _message_from_row(cast(asyncpg.Record, row))


async def fetch_context_messages(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    turn_limit: int,
) -> list[ContextMessage]:
    rows = await pool.fetch(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at DESC, id DESC
        LIMIT $2
        """,
        conversation_id,
        turn_limit * 2,
    )
    ordered_rows = list(reversed(rows))
    return [
        ContextMessage(
            role=cast(Literal["system", "user", "assistant", "tool"], row["role"]),
            content=cast(str, row["content"]),
        )
        for row in ordered_rows
    ]


async def update_conversation_status(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    user_id: UUID,
    status: str,
) -> ConversationSummary | None:
    row = await pool.fetchrow(
        """
        UPDATE conversations
        SET status = $2, updated_at = now()
        WHERE id = $1 AND user_id = $3 AND status <> 'archived'
        RETURNING id, user_id, title, status, model_default, created_at, updated_at
        """,
        conversation_id,
        status,
        user_id,
    )
    if row is None:
        return None
    return _conversation_from_row(row)


async def archive_conversation(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    user_id: UUID,
) -> ConversationSummary | None:
    row = await pool.fetchrow(
        """
        UPDATE conversations
        SET status = 'archived', updated_at = now()
        WHERE id = $1 AND user_id = $2 AND status <> 'archived'
        RETURNING id, user_id, title, status, model_default, created_at, updated_at
        """,
        conversation_id,
        user_id,
    )
    if row is None:
        return None
    return _conversation_from_row(row)


def count_context_turns(messages: Sequence[ContextMessage]) -> int:
    return sum(1 for message in messages if message.role == "user")


async def get_user_by_id(pool: asyncpg.Pool, user_id: UUID) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        """
        SELECT id, email, display_name, created_at
        FROM users
        WHERE id = $1
        """,
        user_id,
    )
    if row is None:
        return None
    return _user_from_row(row)


async def create_anonymous_user(pool: asyncpg.Pool) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO users (email, display_name)
        VALUES (NULL, $1)
        RETURNING id, email, display_name, created_at
        """,
        "Anonymous demo user",
    )
    return _user_from_row(cast(asyncpg.Record, row))
