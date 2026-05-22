from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
)


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


class IngestBatchEnvelope(StrictModel):
    events: list[InferenceLogEvent]


class IngestResponse(BaseModel):
    accepted: int
    deduped: int
    event_ids: list[UUID]
    stream_ids: list[str]


_SINGLE_EVENT_ADAPTER = TypeAdapter(InferenceLogEvent)
_EVENT_LIST_ADAPTER = TypeAdapter(list[InferenceLogEvent])
_EVENT_BATCH_ADAPTER = TypeAdapter(IngestBatchEnvelope)


def parse_events(payload: object) -> list[InferenceLogEvent]:
    if isinstance(payload, list):
        return _EVENT_LIST_ADAPTER.validate_python(payload)
    if isinstance(payload, dict):
        if "events" in payload:
            return _EVENT_BATCH_ADAPTER.validate_python(payload).events
        return [_SINGLE_EVENT_ADAPTER.validate_python(payload)]
    raise ValueError("Request body must be a JSON object, a JSON array, or an {events: [...]} envelope.")
