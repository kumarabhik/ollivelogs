from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeSerializer

from app import repository
from app.deps import PostgresPoolDep, SettingsDep
from app.settings import Settings


@dataclass(slots=True, frozen=True)
class SessionUser:
    user_id: UUID
    display_name: str | None
    is_anonymous: bool


def _serializer(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.session_secret, salt="ollivelogs-session")


def _encode_cookie(settings: Settings, user_id: UUID) -> str:
    return _serializer(settings).dumps({"user_id": str(user_id)})


def _decode_cookie(settings: Settings, cookie_value: str) -> UUID | None:
    try:
        payload = _serializer(settings).loads(cookie_value)
    except BadSignature:
        return None

    user_id = payload.get("user_id")
    if not isinstance(user_id, str):
        return None

    try:
        return UUID(user_id)
    except ValueError:
        return None


def _set_session_cookie(response: Response, settings: Settings, user_id: UUID) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=_encode_cookie(settings, user_id),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


async def resolve_session_user(
    request: Request,
    response: Response,
    postgres_pool: asyncpg.Pool = PostgresPoolDep,
    settings: Settings = SettingsDep,
) -> SessionUser:
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if cookie_value:
        user_id = _decode_cookie(settings, cookie_value)
        if user_id is not None:
            user = await repository.get_user_by_id(postgres_pool, user_id)
            if user is not None:
                return SessionUser(
                    user_id=user["id"],
                    display_name=user["display_name"],
                    is_anonymous=user["email"] is None,
                )

    if not settings.allow_anonymous:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid session cookie is required.",
        )

    user = await repository.create_anonymous_user(postgres_pool)
    _set_session_cookie(response, settings, user["id"])
    return SessionUser(
        user_id=user["id"],
        display_name=user["display_name"],
        is_anonymous=True,
    )


CurrentUserDep = Depends(resolve_session_user)
