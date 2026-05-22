from __future__ import annotations

import asyncio
import json
from typing import Any, Literal, cast

import asyncpg
from clickhouse_connect.driver.client import Client

from log_consumer_app.schemas import ClickHouseRow, InferenceLogEvent, RedactedEventRecord


async def insert_message(
    pool: asyncpg.Pool,
    record: RedactedEventRecord,
    *,
    store_raw: bool,
    raw_encryption_key: str,
) -> bool:
    role = _message_role(record.event)
    content = record.output_preview or record.input_preview or _fallback_error_text(record.event)
    status = _message_status(record.event.status)
    token_count = record.event.usage.completion_tokens or record.event.usage.total_tokens
    if not store_raw or not record.raw_content:
        result = await pool.execute(
            """
            INSERT INTO messages (
                conversation_id,
                role,
                content,
                content_full_id,
                token_count,
                status,
                event_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (event_id) DO NOTHING
            """,
            record.event.conversation_id,
            role,
            content,
            None,
            token_count,
            status,
            record.event.event_id,
        )
        return cast(str, result).endswith("1")

    async with pool.acquire() as conn, conn.transaction():
        content_full_id = await conn.fetchval(
            """
            INSERT INTO messages_full (content_enc)
            VALUES (
                pgp_sym_encrypt(
                    $1::text,
                    $2::text,
                    'cipher-algo=aes256,compress-algo=1'
                )
            )
            RETURNING id
            """,
            record.raw_content,
            raw_encryption_key,
        )
        result = await conn.execute(
            """
            INSERT INTO messages (
                conversation_id,
                role,
                content,
                content_full_id,
                token_count,
                status,
                event_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (event_id) DO NOTHING
            """,
            record.event.conversation_id,
            role,
            content,
            content_full_id,
            token_count,
            status,
            record.event.event_id,
        )
    return cast(str, result).endswith("1")


async def insert_clickhouse_log(client: Client, row: ClickHouseRow) -> None:
    await asyncio.to_thread(
        client.insert,
        table="inference_logs",
        data=[
            [
                row.event_id,
                row.ts,
                row.conversation_id,
                row.user_id,
                row.provider,
                row.model,
                row.status,
                row.error_kind,
                row.latency_ms,
                row.ttft_ms,
                row.prompt_tokens,
                row.completion_tokens,
                row.total_tokens,
                row.cost_usd,
                row.input_preview,
                row.output_preview,
                row.client,
                row.sdk_version,
                row.request_id,
                row.extra,
            ]
        ],
        column_names=[
            "event_id",
            "ts",
            "conversation_id",
            "user_id",
            "provider",
            "model",
            "status",
            "error_kind",
            "latency_ms",
            "ttft_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost_usd",
            "input_preview",
            "output_preview",
            "client",
            "sdk_version",
            "request_id",
            "extra",
        ],
    )


def build_clickhouse_row(record: RedactedEventRecord) -> ClickHouseRow:
    event = record.event
    extra_payload: dict[str, Any] = {
        "request": event.request.model_dump(exclude_none=True),
        "response": event.response.model_dump(exclude_none=True),
    }
    if event.error is not None:
        extra_payload["error"] = event.error.model_dump()
    if event.extra is not None:
        extra_payload["extra"] = event.extra
    return ClickHouseRow(
        event_id=event.event_id,
        ts=event.ts,
        conversation_id=event.conversation_id,
        user_id=event.user_id,
        provider=event.provider,
        model=event.model,
        status=event.status,
        error_kind=event.error.kind if event.error is not None else "",
        latency_ms=event.timing.latency_ms,
        ttft_ms=event.timing.ttft_ms,
        prompt_tokens=event.usage.prompt_tokens,
        completion_tokens=event.usage.completion_tokens,
        total_tokens=event.usage.total_tokens,
        cost_usd=event.cost_usd,
        input_preview=record.input_preview,
        output_preview=record.output_preview,
        client=event.client,
        sdk_version=event.sdk_version,
        request_id=record.request_id,
        extra=json.dumps(extra_payload, separators=(",", ":")),
    )


def _message_role(event: InferenceLogEvent) -> Literal["assistant", "tool"]:
    if event.extra is not None and event.extra.get("message_role") == "tool":
        return "tool"
    return "assistant"


def _message_status(status: str) -> Literal["ok", "error", "cancelled", "partial"]:
    if status == "cancelled":
        return "cancelled"
    if status == "ok":
        return "ok"
    if status == "timeout":
        return "error"
    if status == "partial":
        return "partial"
    return "error"


def _fallback_error_text(event: InferenceLogEvent) -> str:
    if event.error is not None:
        return event.error.message
    return ""
