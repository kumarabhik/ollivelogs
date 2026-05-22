from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.evals import (
    EvalTarget,
    EvalTurnResult,
    build_eval_turns,
    compare_texts,
    format_results_text,
    resolve_eval_target,
    serialise_results,
    summarise_eval_run,
)
from app.schemas import MessageResource


def _message(
    *,
    role: str,
    content: str,
    status: str = "ok",
) -> MessageResource:
    return MessageResource(
        id=uuid4(),
        conversation_id=uuid4(),
        role=role,  # type: ignore[arg-type]
        content=content,
        token_count=None,
        status=status,  # type: ignore[arg-type]
        event_id=None,
        created_at=datetime.now(UTC),
    )


def test_resolve_eval_target_infers_provider_from_model_prefix() -> None:
    target = resolve_eval_target("gpt-4.1", provider_override=None, default_provider="huggingface")
    assert target == EvalTarget(provider="openai", model="gpt-4.1")


def test_resolve_eval_target_honours_explicit_provider_prefix() -> None:
    target = resolve_eval_target(
        "anthropic/claude-3-7-sonnet",
        provider_override=None,
        default_provider="huggingface",
    )
    assert target == EvalTarget(provider="anthropic", model="claude-3-7-sonnet")


def test_build_eval_turns_pairs_completed_user_assistant_turns() -> None:
    messages = [
        _message(role="system", content="You are helpful."),
        _message(role="user", content="Hello"),
        _message(role="assistant", content="Hi there."),
        _message(role="user", content="What is 2 + 2?"),
        _message(role="assistant", content="It is 4."),
    ]

    turns = build_eval_turns(messages)

    assert len(turns) == 2
    assert turns[0].prompt == "Hello"
    assert turns[0].baseline_response == "Hi there."
    assert [message.role for message in turns[1].context] == ["system", "user", "assistant", "user"]


def test_compare_texts_returns_similarity_scores_and_diff() -> None:
    diff = compare_texts("the dragon guards the gate", "the dragon protects the gate")

    assert diff.token_similarity > 0.5
    assert diff.semantic_similarity > 0.5
    assert "--- baseline" in diff.token_diff
    assert "protects" in diff.token_diff


def test_eval_summary_and_serialisation_are_stable() -> None:
    run_id = uuid4()
    conversation_id = uuid4()
    result = EvalTurnResult(
        run_id=run_id,
        ts=datetime.now(UTC),
        conversation_id=conversation_id,
        turn_index=1,
        source_provider="recorded",
        source_model="conversation-history",
        against_provider="openai",
        against_model="gpt-4.1",
        status="ok",
        latency_ms=123,
        prompt_tokens=10,
        completion_tokens=12,
        total_tokens=22,
        cost_usd=0.01,
        token_similarity=0.8,
        semantic_similarity=0.9,
        prompt_preview="hello",
        baseline_preview="hi",
        candidate_preview="hey",
        request_id="req_eval_test",
        token_diff="--- baseline",
    )

    summary = summarise_eval_run([result])
    payload = serialise_results([result])
    text = format_results_text([result])
    summary_payload = payload["summary"]

    assert summary.ok_turns == 1
    assert isinstance(summary_payload, dict)
    assert summary_payload["avg_semantic_similarity"] == 0.9
    assert "against: openai/gpt-4.1" in text
