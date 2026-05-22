from __future__ import annotations

import time
import uuid

from fastapi import HTTPException, status
from redis.asyncio import Redis

from app.auth import SessionUser


class SlidingWindowRateLimiter:
    def __init__(self, window_seconds: int = 60) -> None:
        self._window_seconds = window_seconds

    async def enforce_request_limit(
        self,
        redis_client: Redis,
        *,
        actor: SessionUser,
        bucket: str,
        request_limit: int,
    ) -> None:
        if request_limit <= 0:
            return

        key = self._key(bucket, actor.user_id, "requests")
        now_ms = self._now_ms()
        window_start = now_ms - (self._window_seconds * 1000)

        await redis_client.zremrangebyscore(key, 0, window_start)
        count = await redis_client.zcard(key)
        if count >= request_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Request rate limit exceeded.",
            )

        await redis_client.zadd(key, {self._member(now_ms, 1): now_ms})
        await redis_client.expire(key, self._window_seconds)

    async def enforce_token_limit(
        self,
        redis_client: Redis,
        *,
        actor: SessionUser,
        bucket: str,
        token_limit: int,
        token_cost: int,
    ) -> None:
        if token_limit <= 0 or token_cost <= 0:
            return

        key = self._key(bucket, actor.user_id, "tokens")
        now_ms = self._now_ms()
        window_start = now_ms - (self._window_seconds * 1000)

        await redis_client.zremrangebyscore(key, 0, window_start)
        members = await redis_client.zrange(key, 0, -1)
        current_cost = sum(self._parse_cost(member) for member in members)
        if current_cost + token_cost > token_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Token rate limit exceeded.",
            )

        await redis_client.zadd(key, {self._member(now_ms, token_cost): now_ms})
        await redis_client.expire(key, self._window_seconds)

    def _key(self, bucket: str, user_id: uuid.UUID, kind: str) -> str:
        return f"rate:{bucket}:{kind}:{user_id}"

    def _member(self, now_ms: int, cost: int) -> str:
        return f"{now_ms}:{cost}:{uuid.uuid4().hex}"

    def _parse_cost(self, member: str) -> int:
        parts = member.split(":", 2)
        return int(parts[1]) if len(parts) >= 2 else 0

    def _now_ms(self) -> int:
        return int(time.time() * 1000)
