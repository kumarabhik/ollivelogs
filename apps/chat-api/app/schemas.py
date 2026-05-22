from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

ConversationStatus = Literal["active", "cancelled", "archived"]
MessageRole = Literal["system", "user", "assistant", "tool"]
MessageStatus = Literal["ok", "error", "cancelled", "partial"]

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OptionalTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class ConversationCreateRequest(BaseModel):
    user_id: UUID | None = None
    title: OptionalTitle | None = None
    model_default: str | None = None


class MessageResource(BaseModel):
    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    token_count: int | None = None
    status: MessageStatus
    event_id: UUID | None = None
    created_at: datetime


class ConversationSummary(BaseModel):
    id: UUID
    user_id: UUID | None = None
    title: str | None = None
    status: ConversationStatus
    model_default: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageResource]


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    total: int
    limit: int
    offset: int


class SendMessageRequest(BaseModel):
    content: NonEmptyText
    provider: str | None = None
    model: str | None = None
    stream: bool = True


class SendMessageResponse(BaseModel):
    conversation_id: UUID
    provider: str
    model: str
    context_turns: int
    user_message: MessageResource
    assistant_message: MessageResource


class ConversationStatusResponse(BaseModel):
    conversation_id: UUID
    status: ConversationStatus | Literal["cancel_requested"]


class SseStartEvent(BaseModel):
    conversation_id: UUID
    provider: str
    model: str
    context_turns: int
    user_message_id: UUID


class SseTokenEvent(BaseModel):
    delta: str


class SseDoneEvent(BaseModel):
    message: MessageResource


class SseCancelledEvent(BaseModel):
    message: MessageResource
    reason: str = Field(default="cancelled")


class SseErrorEvent(BaseModel):
    message: MessageResource
    reason: str = Field(default="provider_error")
