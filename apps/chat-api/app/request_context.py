from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import uuid4

_REQUEST_ID: ContextVar[str | None] = ContextVar("chat_api_request_id", default=None)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def resolve_request_id(incoming: str | None) -> str:
    if incoming and incoming.strip() != "":
        return incoming.strip()
    return f"req_{uuid4().hex[:16]}"


@contextmanager
def bind_request_id(request_id: str) -> Iterator[None]:
    token: Token[str | None] = _REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)
