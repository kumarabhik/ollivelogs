from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from app.evals import EvalTurnResult, insert_eval_runs

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_insert_eval_runs_persists_clickhouse_rows(clickhouse_client: object) -> None:
    result = EvalTurnResult(
        run_id=uuid4(),
        ts=datetime.now(UTC),
        conversation_id=uuid4(),
        turn_index=1,
        source_provider="recorded",
        source_model="conversation-history",
        against_provider="openai",
        against_model="gpt-4.1",
        status="ok",
        latency_ms=231,
        prompt_tokens=12,
        completion_tokens=15,
        total_tokens=27,
        cost_usd=0.0021,
        token_similarity=0.81,
        semantic_similarity=0.78,
        prompt_preview="How are you?",
        baseline_preview="I'm good.",
        candidate_preview="Doing well.",
        request_id="req_eval_store",
        token_diff="--- baseline\n+++ candidate",
    )

    client = cast(Any, clickhouse_client)
    await insert_eval_runs(client, [result])

    query = client.query(
        """
        SELECT against_model, status, semantic_similarity
        FROM ollivelogs.eval_runs
        WHERE request_id = 'req_eval_store'
        """
    )
    assert query.first_row[0] == "gpt-4.1"
    assert query.first_row[1] == "ok"
    assert float(query.first_row[2]) == pytest.approx(0.78, abs=1e-6)
