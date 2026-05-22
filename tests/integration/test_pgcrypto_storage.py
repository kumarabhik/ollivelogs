from __future__ import annotations

import os

import asyncpg
import pytest
from log_consumer_app.repository import insert_message
from log_consumer_app.schemas import InferenceLogEvent, RedactedEventRecord

from tests.helpers import build_event

pytestmark = pytest.mark.integration


def _pool_dsn() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://ollive:ollive@localhost:5432/ollivelogs",
    ).replace("+asyncpg", "")


@pytest.mark.asyncio
async def test_messages_full_content_is_pgcrypto_encrypted_and_decryptable(
    pg: asyncpg.Connection,
    fresh_email: str,
) -> None:
    user_id = await pg.fetchval(
        "INSERT INTO users (email, display_name) VALUES ($1, $2) RETURNING id",
        fresh_email,
        "Pgcrypto Tester",
    )
    conversation_id = await pg.fetchval(
        "INSERT INTO conversations (user_id, title) VALUES ($1, $2) RETURNING id",
        user_id,
        "pgcrypto",
    )
    raw_text = "assistant raw Aadhaar 1234 1234 1234"
    event = InferenceLogEvent.model_validate(
        build_event(
            conversation_id=str(conversation_id),
            response_preview=raw_text,
        )
    )
    record = RedactedEventRecord(
        event=event,
        input_preview="user input",
        output_preview="assistant preview <AADHAAR>",
        request_id="req_pgcrypto",
        raw_content=raw_text,
    )
    pool = await asyncpg.create_pool(dsn=_pool_dsn(), min_size=1, max_size=1)
    try:
        inserted = await insert_message(
            pool,
            record,
            store_raw=True,
            raw_encryption_key="secret-key",
        )
    finally:
        await pool.close()

    assert inserted is True
    row = await pg.fetchrow(
        """
        SELECT
            m.content,
            m.content_full_id,
            pgp_sym_decrypt(mf.content_enc, $2::text) AS decrypted_content
        FROM messages AS m
        JOIN messages_full AS mf ON mf.id = m.content_full_id
        WHERE m.event_id = $1
        """,
        event.event_id,
        "secret-key",
    )
    assert row is not None
    assert row["content"] == "assistant preview <AADHAAR>"
    assert row["content_full_id"] is not None
    assert row["decrypted_content"] == raw_text
