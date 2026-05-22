from __future__ import annotations

from dataclasses import dataclass, field
from types import TracebackType
from uuid import UUID

import pytest
from log_consumer_app.repository import build_clickhouse_row, insert_message
from log_consumer_app.schemas import InferenceLogEvent, RedactedEventRecord

from tests.helpers import build_event

pytestmark = pytest.mark.unit


@dataclass(slots=True)
class FakeConnection:
    inserted_full_id: UUID
    fetchval_calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    execute_calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def fetchval(self, query: str, *args: object) -> UUID:
        self.fetchval_calls.append((query, args))
        return self.inserted_full_id

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return "INSERT 0 1"

    def transaction(self) -> _NullAsyncContext:
        return _NullAsyncContext(self)


class _NullAsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type
        del exc
        del tb


@dataclass(slots=True)
class FakePool:
    connection: FakeConnection
    execute_calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return "INSERT 0 1"

    def acquire(self) -> _NullAsyncContext:
        return _NullAsyncContext(self.connection)


def _record(*, response_preview: str = "raw assistant output") -> RedactedEventRecord:
    event = InferenceLogEvent.model_validate(
        build_event(
            request_preview="request with PAN ABCDE1234F",
            response_preview=response_preview,
        )
    )
    return RedactedEventRecord(
        event=event,
        input_preview="request with <PAN>",
        output_preview="assistant with <PAN>",
        request_id="req_unit",
        raw_content=response_preview,
    )


@pytest.mark.asyncio
async def test_insert_message_skips_messages_full_when_store_raw_is_disabled() -> None:
    connection = FakeConnection(inserted_full_id=UUID("11111111-1111-1111-1111-111111111111"))
    pool = FakePool(connection=connection)

    inserted = await insert_message(
        pool,
        _record(),
        store_raw=False,
        raw_encryption_key="secret-key",
    )

    assert inserted is True
    assert connection.fetchval_calls == []
    assert len(pool.execute_calls) == 1
    _, args = pool.execute_calls[0]
    assert args[3] is None


@pytest.mark.asyncio
async def test_insert_message_encrypts_raw_content_when_store_raw_is_enabled() -> None:
    inserted_full_id = UUID("22222222-2222-2222-2222-222222222222")
    connection = FakeConnection(inserted_full_id=inserted_full_id)
    pool = FakePool(connection=connection)

    inserted = await insert_message(
        pool,
        _record(response_preview="assistant raw PAN ABCDE1234F"),
        store_raw=True,
        raw_encryption_key="secret-key",
    )

    assert inserted is True
    assert pool.execute_calls == []
    assert len(connection.fetchval_calls) == 1
    fetch_query, fetch_args = connection.fetchval_calls[0]
    assert "pgp_sym_encrypt" in fetch_query
    assert fetch_args == ("assistant raw PAN ABCDE1234F", "secret-key")

    assert len(connection.execute_calls) == 1
    _, insert_args = connection.execute_calls[0]
    assert insert_args[3] == inserted_full_id


def test_build_clickhouse_row_preserves_tool_call_metadata_in_extra() -> None:
    payload = build_event(
        request_preview="user: please search",
        response_preview="assistant: calling search",
    )
    payload["extra"] = {
        "traceparent": "00-test",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "name": "search_docs",
                "arguments": '{"query":"pricing"}',
            }
        ],
    }
    event = InferenceLogEvent.model_validate(payload)
    row = build_clickhouse_row(
        RedactedEventRecord(
            event=event,
            input_preview="user: please search",
            output_preview="assistant: calling search",
            request_id="req_unit",
            raw_content="assistant: calling search",
        )
    )

    assert '"tool_calls":[{"id":"call_1","type":"function","name":"search_docs","arguments":"{\\"query\\":\\"pricing\\"}"}]' in row.extra
