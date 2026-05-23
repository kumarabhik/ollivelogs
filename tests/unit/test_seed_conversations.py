from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from db.seed.conversations import (
    DEFAULT_DATASET_LIMIT,
    SYNTHETIC_CONVERSATIONS,
    build_seed_conversations,
    conversation_uuid,
    event_uuid,
    load_dataset_conversations,
    message_uuid,
    resolve_dataset_path,
)


def test_load_dataset_conversations_maps_roles_and_limits(tmp_path: Path) -> None:
    dataset_path = tmp_path / "reddit-conversations.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "id": "conv-001",
                    "topic": "Refund request after duplicate charge",
                    "turns": [
                        {"role": "customer", "text": "I was billed twice."},
                        {"role": "agent", "text": "I can help with that."},
                        {"role": "customer", "text": "Thank you."},
                    ],
                },
                {
                    "id": "conv-002",
                    "topic": "Skip because it only has one usable turn",
                    "turns": [
                        {"role": "moderator", "text": "unsupported role"},
                        {"role": "customer", "text": "only one usable turn"},
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

    now = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    conversations = load_dataset_conversations(
        dataset_path,
        limit=DEFAULT_DATASET_LIMIT,
        model_name="Qwen/Qwen2.5-72B-Instruct",
        now=now,
    )

    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation.source_id == "conv-001"
    assert conversation.title == "Refund request after duplicate charge"
    assert conversation.model_default == "Qwen/Qwen2.5-72B-Instruct"
    assert conversation.created_at == now
    assert [turn.role for turn in conversation.turns] == ["user", "assistant", "user"]


def test_build_seed_conversations_appends_dataset_slice(tmp_path: Path) -> None:
    dataset_path = tmp_path / "demo-conversations.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "id": "conv-101",
                    "topic": "Shipping delay update",
                    "turns": [
                        {"role": "customer", "text": "Where is my order?"},
                        {"role": "agent", "text": "It arrives tomorrow."},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    now = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    conversations = build_seed_conversations(
        now,
        dataset_path=dataset_path,
        dataset_enabled=True,
        dataset_limit=1,
        dataset_model="gpt-4.1-mini",
    )

    assert len(conversations) == len(SYNTHETIC_CONVERSATIONS) + 1
    dataset_conversation = conversations[-1]
    assert dataset_conversation.source_id == "conv-101"
    assert dataset_conversation.model_default == "gpt-4.1-mini"
    assert dataset_conversation.turns[0].content == "Where is my order?"


def test_resolve_dataset_path_uses_repo_local_data_dir(tmp_path: Path) -> None:
    repo_root = tmp_path
    data_dir = repo_root / "data"
    data_dir.mkdir()
    dataset_path = data_dir / "reddit-conversations.json"
    dataset_path.write_text("[]", encoding="utf-8")

    resolved = resolve_dataset_path(repo=repo_root)

    assert resolved == dataset_path


def test_seed_ids_are_deterministic() -> None:
    assert conversation_uuid("conv-1") == conversation_uuid("conv-1")
    assert message_uuid("conv-1", 0) == message_uuid("conv-1", 0)
    assert event_uuid("conv-1", 0) == event_uuid("conv-1", 0)
    assert message_uuid("conv-1", 0) != message_uuid("conv-1", 1)
