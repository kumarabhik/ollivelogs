from __future__ import annotations

import json
from dataclasses import dataclass

from redis.asyncio import Redis

from ingest_app.schemas import InferenceLogEvent
from ingest_app.settings import Settings

_PUBLISH_EVENT_SCRIPT = """
local dedup_key = KEYS[1]
local stream_name = KEYS[2]
local payload = ARGV[1]
local ttl = tonumber(ARGV[2])
local maxlen = tonumber(ARGV[3])

if redis.call('SET', dedup_key, '1', 'NX', 'EX', ttl) then
  if maxlen > 0 then
    return redis.call('XADD', stream_name, 'MAXLEN', '~', maxlen, '*', 'payload', payload)
  end
  return redis.call('XADD', stream_name, '*', 'payload', payload)
end

return false
"""


@dataclass(slots=True)
class PublishResult:
    accepted: int
    deduped: int
    stream_ids: list[str]


async def publish_events(
    redis_client: Redis,
    settings: Settings,
    events: list[InferenceLogEvent],
) -> PublishResult:
    accepted = 0
    deduped = 0
    stream_ids: list[str] = []
    for event in events:
        payload = json.dumps(event.model_dump(mode="json"))
        stream_id_result = redis_client.eval(
            _PUBLISH_EVENT_SCRIPT,
            2,
            _dedup_key(event.event_id),
            settings.event_bus_stream,
            payload,
            settings.event_bus_idempotency_ttl_seconds,
            settings.event_bus_stream_maxlen,
        )
        stream_id = (
            await stream_id_result if hasattr(stream_id_result, "__await__") else stream_id_result
        )
        if not stream_id:
            deduped += 1
            continue
        accepted += 1
        stream_ids.append(str(stream_id))
    return PublishResult(accepted=accepted, deduped=deduped, stream_ids=stream_ids)


def _dedup_key(event_id: object) -> str:
    return f"ingest:event:{event_id}"
