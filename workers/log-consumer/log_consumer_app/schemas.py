from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EventRequest(StrictModel):
    messages_preview: StrictStr | None = None
    stream: StrictBool | None = None
    temperature: StrictFloat | None = None
    max_tokens: StrictInt | None = None


class EventResponse(StrictModel):
    text_preview: StrictStr | None = None
    finish_reason: StrictStr | None = None


class EventUsage(StrictModel):
    prompt_tokens: StrictInt = Field(ge=0)
    completion_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)


class EventTiming(StrictModel):
    latency_ms: StrictInt = Field(ge=0)
    ttft_ms: StrictInt = Field(default=0, ge=0)


class EventError(StrictModel):
    kind: StrictStr
    message: StrictStr


class InferenceLogEvent(StrictModel):
    event_id: UUID
    event_schema_version: StrictInt = Field(default=1, ge=1)
    ts: datetime
    conversation_id: UUID
    user_id: UUID | None = None
    client: StrictStr
    sdk_version: StrictStr
    provider: StrictStr
    model: StrictStr
    request: EventRequest
    response: EventResponse
    usage: EventUsage
    timing: EventTiming
    status: StrictStr
    error: EventError | None = None
    cost_usd: StrictFloat = Field(ge=0)
    request_id: StrictStr | None = None
    extra: dict[str, object] | None = None


@dataclass(slots=True)
class RedactedEventRecord:
    event: InferenceLogEvent
    input_preview: str
    output_preview: str
    request_id: str
    raw_content: str


@dataclass(slots=True)
class ClickHouseRow:
    event_id: UUID
    ts: datetime
    conversation_id: UUID
    user_id: UUID | None
    provider: str
    model: str
    status: str
    error_kind: str
    latency_ms: int
    ttft_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    input_preview: str
    output_preview: str
    client: str
    sdk_version: str
    request_id: str
    extra: str


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
