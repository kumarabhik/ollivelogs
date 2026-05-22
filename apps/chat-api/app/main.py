from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from redis.asyncio import Redis
from starlette.responses import Response

from app import repository
from app.auth import CurrentUserDep, SessionUser
from app.db import lifespan, ping_redis
from app.deps import (
    InferenceLoggerDep,
    PostgresPoolDep,
    ProviderRegistryDep,
    RedisDep,
    SettingsDep,
)
from app.inference_logging import InferenceLogger, emit_chat_inference_event
from app.observability import install_observability, metrics_response
from app.providers import (
    CompletionResult,
    CompletionUsage,
    GenerationRequest,
    Provider,
    ProviderConfigurationError,
    ProviderRegistry,
    ProviderStreamChunk,
    ProviderUpstreamError,
)
from app.rate_limit import SlidingWindowRateLimiter
from app.request_context import bind_request_id, current_request_id, resolve_request_id
from app.schemas import (
    ConversationCreateRequest,
    ConversationDetail,
    ConversationListResponse,
    ConversationStatusResponse,
    ConversationSummary,
    MessageResource,
    SendMessageRequest,
    SendMessageResponse,
    SseCancelledEvent,
    SseDoneEvent,
    SseErrorEvent,
    SseStartEvent,
    SseTokenEvent,
)
from app.settings import Settings, get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)
log = logging.getLogger("chat-api")
rate_limiter = SlidingWindowRateLimiter()


@dataclass(slots=True, frozen=True)
class ProviderAttempt:
    provider_name: str
    model_name: str
    provider: Provider
    generation: GenerationRequest


@dataclass(slots=True, frozen=True)
class CompletionExecution:
    result: CompletionResult
    provider_name: str
    model_name: str
    provider: Provider


@dataclass(slots=True, frozen=True)
class CompletedMessageResponse:
    message: MessageResource
    provider_name: str
    model_name: str


@dataclass(slots=True, frozen=True)
class StreamExecutionChunk:
    chunk: ProviderStreamChunk
    provider_name: str
    model_name: str
    provider: Provider


def create_app() -> FastAPI:
    app = FastAPI(
        title="OlliveLogs chat-api",
        version="0.1.0",
        description="Conversations, messages, streaming chat with multi-provider routing.",
        lifespan=lifespan,
    )

    install_observability(app)

    @app.middleware("http")
    async def request_context_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = resolve_request_id(request.headers.get("x-request-id"))
        started_at = time.perf_counter()
        with bind_request_id(request_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "http_request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        return response

    @app.get("/metrics", tags=["meta"], include_in_schema=False)
    async def metrics() -> object:
        return metrics_response()

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "chat-api"})

    @app.get("/readyz", tags=["meta"])
    async def readyz(
        postgres_pool: asyncpg.Pool = PostgresPoolDep,
        redis_client: Redis = RedisDep,
    ) -> JSONResponse:
        await repository.ping_postgres(postgres_pool)
        await ping_redis(redis_client)
        return JSONResponse({"status": "ready"})

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "service": "ollivelogs-chat-api",
            "version": "0.1.0",
            "docs": "/docs",
        }

    @app.post("/v1/conversations", response_model=ConversationSummary, tags=["conversations"])
    async def create_conversation(
        payload: ConversationCreateRequest,
        postgres_pool: asyncpg.Pool = PostgresPoolDep,
        redis_client: Redis = RedisDep,
        app_settings: Settings = SettingsDep,
        current_user: SessionUser = CurrentUserDep,
    ) -> ConversationSummary:
        await rate_limiter.enforce_request_limit(
            redis_client,
            actor=current_user,
            bucket="conversations:create",
            request_limit=app_settings.rate_limit_per_min,
        )
        return await repository.create_conversation(
            postgres_pool,
            current_user.user_id,
            payload.title,
            payload.model_default or app_settings.default_model,
        )

    @app.get("/v1/conversations", response_model=ConversationListResponse, tags=["conversations"])
    async def list_conversations(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        postgres_pool: asyncpg.Pool = PostgresPoolDep,
        current_user: SessionUser = CurrentUserDep,
    ) -> ConversationListResponse:
        total, items = await repository.list_conversations(
            postgres_pool,
            current_user.user_id,
            limit,
            offset,
        )
        return ConversationListResponse(items=items, total=total, limit=limit, offset=offset)

    @app.get(
        "/v1/conversations/{conversation_id}",
        response_model=ConversationDetail,
        tags=["conversations"],
    )
    async def get_conversation(
        conversation_id: UUID,
        postgres_pool: asyncpg.Pool = PostgresPoolDep,
        current_user: SessionUser = CurrentUserDep,
    ) -> ConversationDetail:
        detail = await repository.get_conversation_detail(
            postgres_pool,
            conversation_id,
            current_user.user_id,
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return detail

    @app.post(
        "/v1/conversations/{conversation_id}/messages",
        response_model=SendMessageResponse,
        tags=["messages"],
    )
    async def send_message(
        conversation_id: UUID,
        payload: SendMessageRequest,
        postgres_pool: asyncpg.Pool = PostgresPoolDep,
        redis_client: Redis = RedisDep,
        provider_registry: ProviderRegistry = ProviderRegistryDep,
        inference_logger: InferenceLogger | None = InferenceLoggerDep,
        app_settings: Settings = SettingsDep,
        current_user: SessionUser = CurrentUserDep,
    ) -> SendMessageResponse | StreamingResponse:
        conversation = await repository.get_conversation(
            postgres_pool,
            conversation_id,
            current_user.user_id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        provider_name = payload.provider or app_settings.default_provider
        provider = _resolve_provider(provider_registry, provider_name)
        model_name = payload.model or conversation.model_default or app_settings.default_model
        input_tokens = provider.count_tokens(payload.content, model_name)

        await rate_limiter.enforce_request_limit(
            redis_client,
            actor=current_user,
            bucket="messages:send",
            request_limit=app_settings.rate_limit_per_min,
        )
        await rate_limiter.enforce_token_limit(
            redis_client,
            actor=current_user,
            bucket="messages:send",
            token_limit=app_settings.rate_limit_tokens_per_min,
            token_cost=input_tokens,
        )

        await _clear_cancel_flag(redis_client, conversation_id)
        await repository.update_conversation_status(
            postgres_pool,
            conversation_id,
            current_user.user_id,
            "active",
        )

        user_message = await repository.insert_message(
            postgres_pool,
            conversation_id,
            "user",
            payload.content,
            token_count=input_tokens,
        )
        context_messages = await repository.fetch_context_messages(
            postgres_pool,
            conversation_id,
            app_settings.chat_context_turns,
        )
        context_turns = repository.count_context_turns(context_messages)
        generation = GenerationRequest(
            provider=provider_name,
            model=model_name,
            prompt=payload.content,
            context=context_messages,
            context_turns=context_turns,
            max_tokens=app_settings.provider_max_tokens,
        )

        if payload.stream:
            event_stream = _stream_message_response(
                postgres_pool=postgres_pool,
                redis_client=redis_client,
                current_user=current_user,
                conversation_id=conversation_id,
                user_message_id=user_message.id,
                generation=generation,
                provider=provider,
                provider_registry=provider_registry,
                inference_logger=inference_logger,
                app_settings=app_settings,
                provider_name=provider_name,
                model_name=model_name,
            )
            return StreamingResponse(
                event_stream,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        completed_response = await _complete_message_response(
            postgres_pool=postgres_pool,
            redis_client=redis_client,
            current_user=current_user,
            conversation_id=conversation_id,
            generation=generation,
            provider=provider,
            provider_registry=provider_registry,
            inference_logger=inference_logger,
            app_settings=app_settings,
            provider_name=provider_name,
            model_name=model_name,
        )
        return SendMessageResponse(
            conversation_id=conversation_id,
            provider=completed_response.provider_name,
            model=completed_response.model_name,
            context_turns=context_turns,
            user_message=user_message,
            assistant_message=completed_response.message,
        )

    @app.post(
        "/v1/conversations/{conversation_id}/cancel",
        response_model=ConversationStatusResponse,
        tags=["conversations"],
    )
    async def cancel_conversation(
        conversation_id: UUID,
        postgres_pool: asyncpg.Pool = PostgresPoolDep,
        redis_client: Redis = RedisDep,
        app_settings: Settings = SettingsDep,
        current_user: SessionUser = CurrentUserDep,
    ) -> ConversationStatusResponse:
        await rate_limiter.enforce_request_limit(
            redis_client,
            actor=current_user,
            bucket="conversations:cancel",
            request_limit=app_settings.rate_limit_per_min,
        )
        conversation = await repository.get_conversation(
            postgres_pool,
            conversation_id,
            current_user.user_id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        await redis_client.set(_cancel_key(conversation_id), "1", ex=app_settings.cancel_ttl_seconds)
        await repository.update_conversation_status(
            postgres_pool,
            conversation_id,
            current_user.user_id,
            "cancelled",
        )
        return ConversationStatusResponse(conversation_id=conversation_id, status="cancel_requested")

    @app.delete(
        "/v1/conversations/{conversation_id}",
        response_model=ConversationStatusResponse,
        tags=["conversations"],
    )
    async def delete_conversation(
        conversation_id: UUID,
        postgres_pool: asyncpg.Pool = PostgresPoolDep,
        redis_client: Redis = RedisDep,
        app_settings: Settings = SettingsDep,
        current_user: SessionUser = CurrentUserDep,
    ) -> ConversationStatusResponse:
        await rate_limiter.enforce_request_limit(
            redis_client,
            actor=current_user,
            bucket="conversations:delete",
            request_limit=app_settings.rate_limit_per_min,
        )
        conversation = await repository.archive_conversation(
            postgres_pool,
            conversation_id,
            current_user.user_id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return ConversationStatusResponse(conversation_id=conversation_id, status="archived")

    return app


app = create_app()


async def _complete_message_response(
    *,
    postgres_pool: asyncpg.Pool,
    redis_client: Redis,
    current_user: SessionUser,
    conversation_id: UUID,
    generation: GenerationRequest,
    provider: Provider,
    provider_registry: ProviderRegistry,
    inference_logger: InferenceLogger | None,
    app_settings: Settings,
    provider_name: str,
    model_name: str,
) -> CompletedMessageResponse:
    started_perf = time.perf_counter()
    try:
        execution = await _run_completion(
            provider_name=provider_name,
            model_name=model_name,
            provider=provider,
            generation=generation,
            provider_registry=provider_registry,
            app_settings=app_settings,
        )
    except HTTPException as exc:
        emit_chat_inference_event(
            logger=inference_logger,
            settings=app_settings,
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            provider=provider_name,
            model=model_name,
            request_preview=generation.prompt,
            response_preview="",
            usage=_usage_with_defaults(None, prompt_tokens=provider.count_tokens(generation.prompt, model_name)),
            latency_ms=int((time.perf_counter() - started_perf) * 1000),
            ttft_ms=0,
            status="error",
            stream=False,
            request_id=current_request_id(),
            error_kind="HTTPException",
            error_message=str(exc.detail),
        )
        raise
    result = execution.result
    resolved_provider = execution.provider
    resolved_provider_name = execution.provider_name
    resolved_model_name = execution.model_name
    latency_ms = int((time.perf_counter() - started_perf) * 1000)
    if await _is_cancelled(redis_client, conversation_id):
        assistant_message = await repository.insert_message(
            postgres_pool,
            conversation_id,
            "assistant",
            "",
            status="cancelled",
            token_count=0,
        )
        await repository.update_conversation_status(
            postgres_pool,
            conversation_id,
            current_user.user_id,
            "cancelled",
        )
        emit_chat_inference_event(
            logger=inference_logger,
            settings=app_settings,
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            provider=resolved_provider_name,
            model=resolved_model_name,
            request_preview=generation.prompt,
            response_preview="",
            usage=_usage_with_defaults(
                result.usage,
                prompt_tokens=resolved_provider.count_tokens(generation.prompt, resolved_model_name),
            ),
            latency_ms=latency_ms,
            ttft_ms=0,
            status="cancelled",
            stream=False,
            request_id=current_request_id(),
        )
        await _clear_cancel_flag(redis_client, conversation_id)
        return CompletedMessageResponse(
            message=assistant_message,
            provider_name=resolved_provider_name,
            model_name=resolved_model_name,
        )

    assistant_message = await repository.insert_message(
        postgres_pool,
        conversation_id,
        "assistant",
        result.text,
        token_count=_completion_token_count(result.usage, result.text),
    )
    emit_chat_inference_event(
        logger=inference_logger,
        settings=app_settings,
        conversation_id=conversation_id,
        user_id=current_user.user_id,
        provider=resolved_provider_name,
        model=resolved_model_name,
        request_preview=generation.prompt,
        response_preview=result.text,
        usage=_usage_with_defaults(
            result.usage,
            prompt_tokens=resolved_provider.count_tokens(generation.prompt, resolved_model_name),
        ),
        latency_ms=latency_ms,
        ttft_ms=0,
        status="ok",
        stream=False,
        request_id=current_request_id(),
    )
    await _clear_cancel_flag(redis_client, conversation_id)
    return CompletedMessageResponse(
        message=assistant_message,
        provider_name=resolved_provider_name,
        model_name=resolved_model_name,
    )


async def _stream_message_response(
    *,
    postgres_pool: asyncpg.Pool,
    redis_client: Redis,
    current_user: SessionUser,
    conversation_id: UUID,
    user_message_id: UUID,
    generation: GenerationRequest,
    provider: Provider,
    provider_registry: ProviderRegistry,
    inference_logger: InferenceLogger | None,
    app_settings: Settings,
    provider_name: str,
    model_name: str,
) -> AsyncIterator[str]:
    emitted_text = ""
    final_usage: CompletionUsage | None = None
    started_perf = time.perf_counter()
    ttft_ms = 0
    start_event = SseStartEvent(
        conversation_id=conversation_id,
        provider=provider_name,
        model=model_name,
        context_turns=generation.context_turns,
        user_message_id=user_message_id,
    )
    yield _sse_event("start", start_event.model_dump(mode="json"))

    active_provider = provider
    active_provider_name = provider_name
    active_model_name = model_name

    try:
        async for stream_chunk in _run_stream(
            provider_name=provider_name,
            model_name=model_name,
            provider=provider,
            generation=generation,
            provider_registry=provider_registry,
            app_settings=app_settings,
        ):
            active_provider = stream_chunk.provider
            active_provider_name = stream_chunk.provider_name
            active_model_name = stream_chunk.model_name
            chunk = stream_chunk.chunk
            if await _is_cancelled(redis_client, conversation_id):
                assistant_message = await repository.insert_message(
                    postgres_pool,
                    conversation_id,
                    "assistant",
                    emitted_text,
                    status="cancelled",
                    token_count=_completion_token_count(final_usage, emitted_text),
                )
                await repository.update_conversation_status(
                    postgres_pool,
                    conversation_id,
                    current_user.user_id,
                    "cancelled",
                )
                emit_chat_inference_event(
                    logger=inference_logger,
                    settings=app_settings,
                    conversation_id=conversation_id,
                    user_id=current_user.user_id,
                    provider=active_provider_name,
                    model=active_model_name,
                    request_preview=generation.prompt,
                    response_preview=emitted_text,
                    usage=_usage_with_defaults(
                        final_usage,
                        prompt_tokens=active_provider.count_tokens(generation.prompt, active_model_name),
                        completion_tokens=_estimate_token_count(emitted_text),
                    ),
                    latency_ms=int((time.perf_counter() - started_perf) * 1000),
                    ttft_ms=ttft_ms,
                    status="cancelled",
                    stream=True,
                    request_id=current_request_id(),
                )
                cancelled_event = SseCancelledEvent(message=assistant_message)
                yield _sse_event("cancelled", cancelled_event.model_dump(mode="json"))
                return

            if chunk.usage is not None:
                final_usage = chunk.usage
            if chunk.delta:
                if ttft_ms == 0:
                    ttft_ms = int((time.perf_counter() - started_perf) * 1000)
                emitted_text += chunk.delta
                yield _sse_event(
                    "token",
                    SseTokenEvent(delta=chunk.delta).model_dump(mode="json"),
                )

        assistant_message = await repository.insert_message(
            postgres_pool,
            conversation_id,
            "assistant",
            emitted_text,
            token_count=_completion_token_count(final_usage, emitted_text),
        )
        emit_chat_inference_event(
            logger=inference_logger,
            settings=app_settings,
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            provider=active_provider_name,
            model=active_model_name,
            request_preview=generation.prompt,
            response_preview=emitted_text,
            usage=_usage_with_defaults(
                final_usage,
                prompt_tokens=active_provider.count_tokens(generation.prompt, active_model_name),
                completion_tokens=_estimate_token_count(emitted_text),
            ),
            latency_ms=int((time.perf_counter() - started_perf) * 1000),
            ttft_ms=ttft_ms,
            status="ok",
            stream=True,
            request_id=current_request_id(),
        )
        done_event = SseDoneEvent(message=assistant_message)
        yield _sse_event("done", done_event.model_dump(mode="json"))
    except ProviderUpstreamError as exc:
        error_message = await repository.insert_message(
            postgres_pool,
            conversation_id,
            "assistant",
            emitted_text,
            status="error",
            token_count=_completion_token_count(final_usage, emitted_text),
        )
        emit_chat_inference_event(
            logger=inference_logger,
            settings=app_settings,
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            provider=active_provider_name,
            model=active_model_name,
            request_preview=generation.prompt,
            response_preview=emitted_text,
            usage=_usage_with_defaults(
                final_usage,
                prompt_tokens=active_provider.count_tokens(generation.prompt, active_model_name),
                completion_tokens=_estimate_token_count(emitted_text),
            ),
            latency_ms=int((time.perf_counter() - started_perf) * 1000),
            ttft_ms=ttft_ms,
            status="error",
            stream=True,
            request_id=current_request_id(),
            error_kind=type(exc).__name__,
            error_message=str(exc),
        )
        error_event = SseErrorEvent(message=error_message, reason=str(exc))
        yield _sse_event("error", error_event.model_dump(mode="json"))
    finally:
        await _clear_cancel_flag(redis_client, conversation_id)


def _resolve_provider(provider_registry: ProviderRegistry, provider_name: str) -> Provider:
    if not provider_registry.has(provider_name):
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider_name}'.")
    return provider_registry.get(provider_name)


def _cancel_key(conversation_id: UUID) -> str:
    return f"chat:cancel:{conversation_id}"


async def _is_cancelled(redis_client: Redis, conversation_id: UUID) -> bool:
    value = redis_client.get(_cancel_key(conversation_id))
    if hasattr(value, "__await__"):
        value = await value
    return value == "1"


async def _clear_cancel_flag(redis_client: Redis, conversation_id: UUID) -> None:
    await redis_client.delete(_cancel_key(conversation_id))


def _estimate_token_count(text: str) -> int:
    stripped = text.strip()
    return len(stripped.split()) if stripped else 0


def _completion_token_count(usage: CompletionUsage | None, text: str) -> int:
    if usage is not None and usage.completion_tokens is not None:
        return usage.completion_tokens
    return _estimate_token_count(text)


def _usage_with_defaults(
    usage: CompletionUsage | None,
    *,
    prompt_tokens: int,
    completion_tokens: int | None = None,
) -> CompletionUsage:
    if usage is None:
        resolved_completion_tokens = (
            completion_tokens if completion_tokens is not None else 0
        )
        return CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=resolved_completion_tokens,
            total_tokens=prompt_tokens + resolved_completion_tokens,
            cost_usd=0.0,
        )

    resolved_completion_tokens = (
        usage.completion_tokens
        if usage.completion_tokens is not None
        else (completion_tokens if completion_tokens is not None else 0)
    )
    resolved_total_tokens = (
        usage.total_tokens
        if usage.total_tokens is not None
        else prompt_tokens + resolved_completion_tokens
    )
    return CompletionUsage(
        prompt_tokens=usage.prompt_tokens if usage.prompt_tokens is not None else prompt_tokens,
        completion_tokens=resolved_completion_tokens,
        total_tokens=resolved_total_tokens,
        cost_usd=usage.cost_usd,
    )


def _sse_event(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


async def _run_completion(
    *,
    provider_name: str,
    model_name: str,
    provider: Provider,
    generation: GenerationRequest,
    provider_registry: ProviderRegistry,
    app_settings: Settings,
) -> CompletionExecution:
    last_exc: Exception | None = None
    for attempt in _provider_attempts(
        provider_name=provider_name,
        model_name=model_name,
        provider=provider,
        generation=generation,
        provider_registry=provider_registry,
        app_settings=app_settings,
    ):
        try:
            result = await attempt.provider.complete(attempt.generation)
            return CompletionExecution(
                result=result,
                provider_name=attempt.provider_name,
                model_name=attempt.model_name,
                provider=attempt.provider,
            )
        except Exception as exc:
            last_exc = exc
            if not _can_failover_from_exception(exc):
                break
            log.warning(
                "provider_complete_failover",
                extra={
                    "from_provider": attempt.provider_name,
                    "to_provider_candidates": app_settings.failover_targets(attempt.provider_name),
                    "reason": str(exc),
                },
            )

    if app_settings.app_env == "local":
        log.warning("provider_complete_fallback", exc_info=last_exc)
        demo_generation = replace(
            generation,
            provider="demo",
            model=app_settings.provider_default_model("demo"),
        )
        demo_result = await provider_registry.demo_provider.complete(demo_generation)
        return CompletionExecution(
            result=demo_result,
            provider_name="demo",
            model_name=demo_generation.model,
            provider=provider_registry.demo_provider,
        )
    if isinstance(last_exc, ProviderUpstreamError):
        raise HTTPException(status_code=502, detail=str(last_exc)) from last_exc
    raise HTTPException(status_code=503, detail="Requested provider is not configured.") from last_exc


async def _run_stream(
    *,
    provider_name: str,
    model_name: str,
    provider: Provider,
    generation: GenerationRequest,
    provider_registry: ProviderRegistry,
    app_settings: Settings,
) -> AsyncIterator[StreamExecutionChunk]:
    last_exc: Exception | None = None
    for attempt in _provider_attempts(
        provider_name=provider_name,
        model_name=model_name,
        provider=provider,
        generation=generation,
        provider_registry=provider_registry,
        app_settings=app_settings,
    ):
        emitted_chunks = False
        try:
            async for chunk in attempt.provider.stream(attempt.generation):
                emitted_chunks = True
                yield StreamExecutionChunk(
                    chunk=chunk,
                    provider_name=attempt.provider_name,
                    model_name=attempt.model_name,
                    provider=attempt.provider,
                )
            return
        except Exception as exc:
            last_exc = exc
            if emitted_chunks or not _can_failover_from_exception(exc):
                break
            log.warning(
                "provider_stream_failover",
                extra={
                    "from_provider": attempt.provider_name,
                    "to_provider_candidates": app_settings.failover_targets(attempt.provider_name),
                    "reason": str(exc),
                },
            )

    if app_settings.app_env == "local":
        log.warning("provider_stream_fallback", exc_info=last_exc)
        demo_generation = replace(
            generation,
            provider="demo",
            model=app_settings.provider_default_model("demo"),
        )
        async for chunk in provider_registry.demo_provider.stream(demo_generation):
            yield StreamExecutionChunk(
                chunk=chunk,
                provider_name="demo",
                model_name=demo_generation.model,
                provider=provider_registry.demo_provider,
            )
        return
    if isinstance(last_exc, ProviderUpstreamError):
        raise last_exc
    raise ProviderUpstreamError("Requested provider is not configured.") from last_exc


def _provider_attempts(
    *,
    provider_name: str,
    model_name: str,
    provider: Provider,
    generation: GenerationRequest,
    provider_registry: ProviderRegistry,
    app_settings: Settings,
) -> list[ProviderAttempt]:
    attempts = [
        ProviderAttempt(
            provider_name=provider_name,
            model_name=model_name,
            provider=provider,
            generation=generation,
        )
    ]
    if not app_settings.provider_failover_enabled:
        return attempts

    seen = {provider_name}
    for fallback_name in app_settings.failover_targets(provider_name):
        if fallback_name in seen or not provider_registry.has(fallback_name):
            continue
        seen.add(fallback_name)
        fallback_model = app_settings.provider_default_model(fallback_name)
        attempts.append(
            ProviderAttempt(
                provider_name=fallback_name,
                model_name=fallback_model,
                provider=provider_registry.get(fallback_name),
                generation=replace(
                    generation,
                    provider=fallback_name,
                    model=fallback_model,
                ),
            )
        )
    return attempts


def _can_failover_from_exception(exc: Exception) -> bool:
    return isinstance(exc, (ProviderUpstreamError, ProviderConfigurationError))
