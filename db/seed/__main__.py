"""Seed Postgres with demo users and conversations.

The default seed is lightweight synthetic data. If a local dataset is available
under `data/` or `OLLIVE_SEED_DATASET_PATH` is set, a small deterministic slice
of realistic conversations is imported as well.

Run inside the chat-api container:
    docker compose exec chat-api python -m db.seed

Or locally (with DATABASE_URL pointing at the container):
    python -m db.seed
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from db.seed.conversations import (
    USERS,
    build_seed_conversations,
    conversation_uuid,
    event_uuid,
    message_uuid,
    resolve_dataset_path,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://ollive:ollive@localhost:5432/ollivelogs",
).replace("+asyncpg", "")


async def seed() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        user_ids = await _seed_users(conn)
        now = datetime.now(tz=UTC)
        dataset_path = resolve_dataset_path(os.getenv("OLLIVE_SEED_DATASET_PATH"))
        conversations = build_seed_conversations(now, dataset_path=dataset_path)

        for index, conversation in enumerate(conversations):
            owner = user_ids[index % len(user_ids)]
            conversation_id = conversation_uuid(conversation.source_id)
            await conn.execute(
                """
                INSERT INTO conversations (id, user_id, title, model_default, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $5)
                ON CONFLICT (id) DO NOTHING
                """,
                conversation_id,
                owner,
                conversation.title,
                conversation.model_default,
                conversation.created_at,
            )

            for turn_index, turn in enumerate(conversation.turns):
                created_at = conversation.created_at + timedelta(seconds=turn_index)
                await conn.execute(
                    """
                    INSERT INTO messages (
                      id,
                      conversation_id,
                      role,
                      content,
                      token_count,
                      event_id,
                      created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT DO NOTHING
                    """,
                    message_uuid(conversation.source_id, turn_index),
                    conversation_id,
                    turn.role,
                    turn.content,
                    max(1, len(turn.content) // 4),
                    event_uuid(conversation.source_id, turn_index),
                    created_at,
                )

        counts = await conn.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM users)         AS users,
              (SELECT count(*) FROM conversations) AS conversations,
              (SELECT count(*) FROM messages)      AS messages
            """
        )
        dataset_summary = "dataset=none"
        if dataset_path is not None:
            dataset_summary = f"dataset={dataset_path}"
        print(f"seed complete: {dict(counts)} ({dataset_summary})")
    finally:
        await conn.close()


async def _seed_users(conn: asyncpg.Connection) -> list[uuid.UUID]:
    user_ids: list[uuid.UUID] = []
    for email, name in USERS:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, display_name)
            VALUES ($1, $2)
            ON CONFLICT (email) DO UPDATE SET display_name = EXCLUDED.display_name
            RETURNING id
            """,
            email,
            name,
        )
        user_ids.append(row["id"])
    return user_ids


if __name__ == "__main__":
    asyncio.run(seed())
