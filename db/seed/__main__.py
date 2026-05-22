"""Seed Postgres with 3 users, 5 conversations, ~30 messages.

Idempotent: rerun is safe — uses `ON CONFLICT DO NOTHING` on natural keys.

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

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://ollive:ollive@localhost:5432/ollivelogs",
).replace("+asyncpg", "")


USERS = [
    ("kumar@example.com", "Abhishek Kumar"),
    ("alice@example.com", "Alice Carter"),
    ("bob@example.com", "Bob Mehta"),
]

CONVERSATIONS = [
    ("Quick FastAPI question", "gpt-4.1-mini"),
    ("Debugging asyncio cancel", "claude-3-5-sonnet"),
    ("Postgres schema review", "Qwen/Qwen2.5-72B-Instruct"),
    ("LangChain alternative ideas", "gpt-4.1-mini"),
    ("ClickHouse vs Timescale", "claude-3-5-sonnet"),
]

TURNS = [
    ("user", "Hey, can you help me with this?"),
    ("assistant", "Of course — share the code or context."),
    ("user", "Here it is: <snippet>"),
    ("assistant", "Got it. The issue is X — try this fix."),
    ("user", "Worked, thanks!"),
    ("assistant", "Anytime. Anything else?"),
]


async def seed() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
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

        now = datetime.now(tz=UTC)
        conv_ids: list[uuid.UUID] = []
        for i, (title, model) in enumerate(CONVERSATIONS):
            owner = user_ids[i % len(user_ids)]
            created = now - timedelta(hours=i * 4)
            row = await conn.fetchrow(
                """
                INSERT INTO conversations (user_id, title, model_default, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $4)
                RETURNING id
                """,
                owner,
                title,
                model,
                created,
            )
            conv_ids.append(row["id"])

        for conv_id in conv_ids:
            for j, (role, content) in enumerate(TURNS):
                await conn.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content, token_count, event_id)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    conv_id,
                    role,
                    content,
                    len(content) // 4,
                    uuid.uuid4(),
                )

        counts = await conn.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM users)         AS users,
              (SELECT count(*) FROM conversations) AS conversations,
              (SELECT count(*) FROM messages)      AS messages
            """
        )
        print(f"✓ seed complete: {dict(counts)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
