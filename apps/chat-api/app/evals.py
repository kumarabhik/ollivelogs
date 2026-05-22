from __future__ import annotations

import asyncio
import difflib
import json
import math
import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import clickhouse_connect
import httpx
from clickhouse_connect.driver.client import Client

from app import repository
from app.pricing import PricingCatalog
from app.providers import (
    CompletionResult,
    CompletionUsage,
    ContextMessage,
    GenerationRequest,
    Provider,
    ProviderRegistry,
    ProviderUpstreamError,
    build_provider_registry,
)
from app.schemas import MessageResource
from app.settings import Settings

_TOKEN_RE = re.compile(r"[a-z0-9']+")
_KNOWN_PROVIDERS = {"openai", "anthropic", "gemini", "deepseek", "grok", "huggingface", "demo"}


@dataclass(slots=True, frozen=True)
class EvalTarget:
    provider: str
    model: str


@dataclass(slots=True, frozen=True)
class EvalTurn:
    turn_index: int
    user_message_id: UUID
    baseline_message_id: UUID
    prompt: str
    baseline_response: str
    context: tuple[ContextMessage, ...]


@dataclass(slots=True, frozen=True)
class EvalDiff:
    token_similarity: float
    semantic_similarity: float
    token_diff: str


@dataclass(slots=True, frozen=True)
class EvalTurnResult:
    run_id: UUID
    ts: datetime
    conversation_id: UUID
    turn_index: int
    source_provider: str
    source_model: str
    against_provider: str
    against_model: str
    status: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    token_similarity: float
    semantic_similarity: float
    prompt_preview: str
    baseline_preview: str
    candidate_preview: str
    request_id: str
    token_diff: str
    finish_reason: str | None = None
    error_kind: str | None = None
    error_message: str | None = None


@dataclass(slots=True, frozen=True)
class EvalRunSummary:
    run_id: UUID
    conversation_id: UUID
    against_provider: str
    against_model: str
    replayed_turns: int
    avg_token_similarity: float
    avg_semantic_similarity: float
    ok_turns: int
    error_turns: int


async def create_clickhouse_client(settings: Settings) -> Client:
    return await asyncio.to_thread(
        clickhouse_connect.get_client,
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )


def resolve_eval_target(
    against: str,
    *,
    provider_override: str | None,
    default_provider: str,
) -> EvalTarget:
    stripped = against.strip()
    if stripped == "":
        raise ValueError("`--against` must not be empty.")

    if "/" in stripped:
        provider_candidate, model_candidate = stripped.split("/", 1)
        normalized_provider = provider_candidate.lower().strip()
        if normalized_provider in _KNOWN_PROVIDERS and model_candidate.strip() != "":
            return EvalTarget(provider=normalized_provider, model=model_candidate.strip())

    provider = provider_override or _infer_provider_from_model(stripped) or default_provider
    return EvalTarget(provider=provider, model=stripped)


def build_eval_turns(messages: Sequence[MessageResource]) -> list[EvalTurn]:
    context_so_far: list[ContextMessage] = []
    pending_turn: tuple[MessageResource, tuple[ContextMessage, ...]] | None = None
    turns: list[EvalTurn] = []

    for message in messages:
        if message.role == "user":
            context_so_far.append(ContextMessage(role="user", content=message.content))
            pending_turn = (message, tuple(context_so_far))
            continue

        if message.role == "assistant" and pending_turn is not None and message.content.strip() != "":
            user_message, context = pending_turn
            turns.append(
                EvalTurn(
                    turn_index=len(turns) + 1,
                    user_message_id=user_message.id,
                    baseline_message_id=message.id,
                    prompt=user_message.content,
                    baseline_response=message.content,
                    context=context,
                )
            )
            pending_turn = None

        if message.role in {"system", "assistant", "tool"}:
            context_so_far.append(ContextMessage(role=message.role, content=message.content))

    return turns


def compare_texts(baseline: str, candidate: str) -> EvalDiff:
    baseline_tokens = _tokenize(baseline)
    candidate_tokens = _tokenize(candidate)
    token_similarity = round(
        difflib.SequenceMatcher(a=baseline_tokens, b=candidate_tokens).ratio(),
        4,
    )
    semantic_similarity = round(
        _cosine_similarity(Counter(baseline_tokens), Counter(candidate_tokens)),
        4,
    )
    diff_lines = list(
        difflib.unified_diff(
            baseline_tokens,
            candidate_tokens,
            fromfile="baseline",
            tofile="candidate",
            lineterm="",
        )
    )
    return EvalDiff(
        token_similarity=token_similarity,
        semantic_similarity=semantic_similarity,
        token_diff=_truncate("\n".join(diff_lines), 4000),
    )


async def replay_conversation(
    *,
    postgres_pool: asyncpg.Pool,
    provider_registry: ProviderRegistry,
    settings: Settings,
    conversation_id: UUID,
    against: EvalTarget,
    source_provider: str = "recorded",
    source_model: str = "conversation-history",
) -> list[EvalTurnResult]:
    messages = await repository.get_messages(postgres_pool, conversation_id)
    turns = build_eval_turns(messages)
    if not turns:
        raise ValueError("Conversation does not contain any completed user/assistant turns to replay.")

    if not provider_registry.has(against.provider):
        raise ValueError(f"Unknown provider '{against.provider}'.")
    provider = provider_registry.get(against.provider)
    run_id = uuid4()
    request_id = f"req_eval_{uuid4().hex[:12]}"
    results: list[EvalTurnResult] = []
    for turn in turns:
        results.append(
            await _replay_turn(
                run_id=run_id,
                request_id=request_id,
                conversation_id=conversation_id,
                turn=turn,
                provider=provider,
                against=against,
                provider_registry=provider_registry,
                settings=settings,
                source_provider=source_provider,
                source_model=source_model,
            )
        )
    return results


def summarise_eval_run(results: Sequence[EvalTurnResult]) -> EvalRunSummary:
    if not results:
        raise ValueError("At least one eval result is required.")
    ok_results = [result for result in results if result.status == "ok"]
    avg_token_similarity = round(
        sum(result.token_similarity for result in ok_results) / max(len(ok_results), 1),
        4,
    )
    avg_semantic_similarity = round(
        sum(result.semantic_similarity for result in ok_results) / max(len(ok_results), 1),
        4,
    )
    return EvalRunSummary(
        run_id=results[0].run_id,
        conversation_id=results[0].conversation_id,
        against_provider=results[0].against_provider,
        against_model=results[0].against_model,
        replayed_turns=len(results),
        avg_token_similarity=avg_token_similarity,
        avg_semantic_similarity=avg_semantic_similarity,
        ok_turns=len(ok_results),
        error_turns=len(results) - len(ok_results),
    )


async def insert_eval_runs(client: Client, results: Sequence[EvalTurnResult]) -> None:
    if not results:
        return

    rows = [
        [
            str(result.run_id),
            result.ts,
            str(result.conversation_id),
            result.turn_index,
            result.source_provider,
            result.source_model,
            result.against_provider,
            result.against_model,
            result.status,
            result.latency_ms,
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
            result.cost_usd,
            result.token_similarity,
            result.semantic_similarity,
            result.prompt_preview,
            result.baseline_preview,
            result.candidate_preview,
            result.request_id,
            json.dumps(
                {
                    "token_diff": result.token_diff,
                    "finish_reason": result.finish_reason,
                    "error_kind": result.error_kind,
                    "error_message": result.error_message,
                },
                separators=(",", ":"),
            ),
        ]
        for result in results
    ]
    await asyncio.to_thread(
        client.insert,
        table="eval_runs",
        data=rows,
        column_names=[
            "run_id",
            "ts",
            "conversation_id",
            "turn_index",
            "source_provider",
            "source_model",
            "against_provider",
            "against_model",
            "status",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost_usd",
            "token_similarity",
            "semantic_similarity",
            "prompt_preview",
            "baseline_preview",
            "candidate_preview",
            "request_id",
            "extra",
        ],
    )


async def build_runtime(settings: Settings) -> tuple[asyncpg.Pool, httpx.AsyncClient, ProviderRegistry, Client]:
    postgres_pool = await asyncpg.create_pool(
        dsn=settings.database_url.replace("+asyncpg", ""),
        min_size=1,
        max_size=4,
        command_timeout=30,
    )
    http_client = httpx.AsyncClient(timeout=settings.provider_timeout_seconds)
    pricing = PricingCatalog.from_repo_file()
    provider_registry = build_provider_registry(settings, http_client, pricing)
    clickhouse_client = await create_clickhouse_client(settings)
    return postgres_pool, http_client, provider_registry, clickhouse_client


async def close_runtime(
    postgres_pool: asyncpg.Pool,
    http_client: httpx.AsyncClient,
    clickhouse_client: Client,
) -> None:
    await http_client.aclose()
    await postgres_pool.close()
    await asyncio.to_thread(clickhouse_client.close)


async def _replay_turn(
    *,
    run_id: UUID,
    request_id: str,
    conversation_id: UUID,
    turn: EvalTurn,
    provider: Provider,
    against: EvalTarget,
    provider_registry: ProviderRegistry,
    settings: Settings,
    source_provider: str,
    source_model: str,
) -> EvalTurnResult:
    generation = GenerationRequest(
        provider=against.provider,
        model=against.model,
        prompt=turn.prompt,
        context=turn.context,
        context_turns=sum(1 for message in turn.context if message.role == "user"),
        max_tokens=settings.provider_max_tokens,
    )
    started_at = time.perf_counter()
    try:
        completion = await _complete_with_fallback(
            provider,
            generation,
            provider_registry=provider_registry,
            settings=settings,
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        diff = compare_texts(turn.baseline_response, completion.text)
        usage = _usage_defaults(completion.usage, turn.prompt, against.model, provider)
        return EvalTurnResult(
            run_id=run_id,
            ts=datetime.now(UTC),
            conversation_id=conversation_id,
            turn_index=turn.turn_index,
            source_provider=source_provider,
            source_model=source_model,
            against_provider=against.provider,
            against_model=against.model,
            status="ok",
            latency_ms=max(latency_ms, 0),
            prompt_tokens=usage.prompt_tokens or 0,
            completion_tokens=usage.completion_tokens or 0,
            total_tokens=usage.total_tokens or 0,
            cost_usd=usage.cost_usd or 0.0,
            token_similarity=diff.token_similarity,
            semantic_similarity=diff.semantic_similarity,
            prompt_preview=_truncate(turn.prompt, 256),
            baseline_preview=_truncate(turn.baseline_response, 256),
            candidate_preview=_truncate(completion.text, 256),
            request_id=request_id,
            token_diff=diff.token_diff,
            finish_reason=completion.finish_reason,
        )
    except Exception as exc:
        return EvalTurnResult(
            run_id=run_id,
            ts=datetime.now(UTC),
            conversation_id=conversation_id,
            turn_index=turn.turn_index,
            source_provider=source_provider,
            source_model=source_model,
            against_provider=against.provider,
            against_model=against.model,
            status="error",
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            prompt_tokens=provider.count_tokens(turn.prompt, against.model),
            completion_tokens=0,
            total_tokens=provider.count_tokens(turn.prompt, against.model),
            cost_usd=0.0,
            token_similarity=0.0,
            semantic_similarity=0.0,
            prompt_preview=_truncate(turn.prompt, 256),
            baseline_preview=_truncate(turn.baseline_response, 256),
            candidate_preview="",
            request_id=request_id,
            token_diff="",
            error_kind=type(exc).__name__,
            error_message=str(exc),
        )


async def _complete_with_fallback(
    provider: Provider,
    generation: GenerationRequest,
    *,
    provider_registry: ProviderRegistry,
    settings: Settings,
) -> CompletionResult:
    try:
        return await provider.complete(generation)
    except Exception as exc:
        if settings.app_env == "local":
            return await provider_registry.demo_provider.complete(generation)
        if isinstance(exc, ProviderUpstreamError):
            raise
        raise RuntimeError("Requested provider is not configured.") from exc


def _usage_defaults(
    usage: CompletionUsage,
    prompt: str,
    model: str,
    provider: Provider,
) -> CompletionUsage:
    prompt_tokens = usage.prompt_tokens or provider.count_tokens(prompt, model)
    completion_tokens = usage.completion_tokens or 0
    total_tokens = usage.total_tokens or (prompt_tokens + completion_tokens)
    cost_usd = usage.cost_usd if usage.cost_usd is not None else (provider.price(model, prompt_tokens, completion_tokens) or 0.0)
    return CompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )


def serialise_results(results: Sequence[EvalTurnResult]) -> dict[str, object]:
    summary = summarise_eval_run(results)
    return {
        "summary": asdict(summary),
        "turns": [asdict(result) for result in results],
    }


def format_results_text(results: Sequence[EvalTurnResult]) -> str:
    summary = summarise_eval_run(results)
    lines = [
        f"run_id: {summary.run_id}",
        f"conversation_id: {summary.conversation_id}",
        f"against: {summary.against_provider}/{summary.against_model}",
        f"turns: {summary.replayed_turns} (ok={summary.ok_turns}, error={summary.error_turns})",
        f"avg_token_similarity: {summary.avg_token_similarity:.4f}",
        f"avg_semantic_similarity: {summary.avg_semantic_similarity:.4f}",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"turn {result.turn_index}: status={result.status} latency_ms={result.latency_ms} "
                f"token_similarity={result.token_similarity:.4f} semantic_similarity={result.semantic_similarity:.4f}",
                f"baseline: {_truncate(result.baseline_preview, 120)}",
                f"candidate: {_truncate(result.candidate_preview, 120)}",
            ]
        )
        if result.token_diff:
            diff_lines = result.token_diff.splitlines()
            preview = "\n".join(diff_lines[:8])
            lines.append("token_diff:")
            lines.append(preview)
        if result.error_message:
            lines.append(f"error: {result.error_kind}: {result.error_message}")
        lines.append("")
    return "\n".join(lines).strip()


def _infer_provider_from_model(model: str) -> str | None:
    normalized = model.strip().lower()
    if normalized.startswith("gpt-") or normalized.startswith("o1") or normalized.startswith("o3"):
        return "openai"
    if normalized.startswith("claude"):
        return "anthropic"
    if normalized.startswith("gemini"):
        return "gemini"
    if normalized.startswith("deepseek"):
        return "deepseek"
    if normalized.startswith("grok"):
        return "grok"
    if "/" in normalized:
        return "huggingface"
    return None


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in shared)
    if numerator == 0:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
