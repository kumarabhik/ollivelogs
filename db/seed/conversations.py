from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DATASET_LIMIT = 6
DEFAULT_DATASET_MODEL = "Qwen/Qwen2.5-72B-Instruct"
SEED_NAMESPACE = uuid.UUID("fb1fda28-487f-4281-a694-a2ae2a0d0a45")

USERS = [
    ("kumar@example.com", "Abhishek Kumar"),
    ("alice@example.com", "Alice Carter"),
    ("bob@example.com", "Bob Mehta"),
]

SYNTHETIC_CONVERSATIONS = [
    ("Quick FastAPI question", "gpt-4.1-mini"),
    ("Debugging asyncio cancel", "claude-3-5-sonnet"),
    ("Postgres schema review", "Qwen/Qwen2.5-72B-Instruct"),
    ("LangChain alternative ideas", "gpt-4.1-mini"),
    ("ClickHouse vs Timescale", "claude-3-5-sonnet"),
]

SYNTHETIC_TURNS = [
    ("user", "Hey, can you help me with this?"),
    ("assistant", "Of course - share the code or context."),
    ("user", "Here it is: <snippet>"),
    ("assistant", "Got it. The issue is X - try this fix."),
    ("user", "Worked, thanks!"),
    ("assistant", "Anytime. Anything else?"),
]

ROLE_MAP = {
    "customer": "user",
    "agent": "assistant",
    "user": "user",
    "assistant": "assistant",
    "system": "system",
    "tool": "tool",
}


@dataclass(frozen=True, slots=True)
class SeedTurn:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class SeedConversation:
    source_id: str
    title: str
    model_default: str
    created_at: datetime
    turns: tuple[SeedTurn, ...]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_dataset_path(
    explicit_path: str | None = None,
    *,
    repo: Path | None = None,
) -> Path | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        return candidate.resolve() if candidate.is_file() else None

    search_root = repo or repo_root()
    for relative_path in ("data/reddit-conversations.json", "data/demo-conversations.json"):
        candidate = search_root / relative_path
        if candidate.is_file():
            return candidate

    return None


def seed_dataset_enabled() -> bool:
    raw = os.getenv("OLLIVE_SEED_DATASET_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def seed_dataset_limit() -> int:
    raw = os.getenv("OLLIVE_SEED_DATASET_LIMIT", str(DEFAULT_DATASET_LIMIT)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_DATASET_LIMIT


def seed_dataset_model() -> str:
    return os.getenv("OLLIVE_SEED_DATASET_MODEL", DEFAULT_DATASET_MODEL).strip() or DEFAULT_DATASET_MODEL


def conversation_uuid(source_id: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"conversation:{source_id}")


def message_uuid(source_id: str, turn_index: int) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"message:{source_id}:{turn_index}")


def event_uuid(source_id: str, turn_index: int) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"event:{source_id}:{turn_index}")


def synthetic_seed_conversations(now: datetime) -> list[SeedConversation]:
    conversations: list[SeedConversation] = []
    for index, (title, model_default) in enumerate(SYNTHETIC_CONVERSATIONS):
        created_at = now - timedelta(hours=index * 4)
        turns = tuple(SeedTurn(role=role, content=content) for role, content in SYNTHETIC_TURNS)
        conversations.append(
            SeedConversation(
                source_id=f"synthetic-{index + 1}",
                title=title,
                model_default=model_default,
                created_at=created_at,
                turns=turns,
            )
        )

    return conversations


def load_dataset_conversations(
    path: Path,
    *,
    limit: int,
    model_name: str,
    now: datetime,
) -> list[SeedConversation]:
    raw_records = json.loads(path.read_text(encoding="utf-8"))
    conversations: list[SeedConversation] = []

    for index, record in enumerate(raw_records):
        if len(conversations) >= limit:
            break

        source_id = str(record.get("id") or f"dataset-{index + 1:06d}")
        raw_title = str(record.get("topic") or record.get("title") or source_id)
        turns = _normalize_turns(record.get("turns"))
        if len(turns) < 2:
            continue

        conversations.append(
            SeedConversation(
                source_id=source_id,
                title=_normalize_title(raw_title, fallback=source_id),
                model_default=model_name,
                created_at=now - timedelta(minutes=index * 7),
                turns=tuple(turns),
            )
        )

    return conversations


def build_seed_conversations(
    now: datetime,
    *,
    dataset_path: Path | None = None,
    dataset_enabled: bool | None = None,
    dataset_limit: int | None = None,
    dataset_model: str | None = None,
) -> list[SeedConversation]:
    conversations = synthetic_seed_conversations(now)
    enabled = seed_dataset_enabled() if dataset_enabled is None else dataset_enabled
    if not enabled:
        return conversations

    resolved_path = dataset_path or resolve_dataset_path(os.getenv("OLLIVE_SEED_DATASET_PATH"))
    if resolved_path is None:
        return conversations

    limit = seed_dataset_limit() if dataset_limit is None else max(0, dataset_limit)
    if limit == 0:
        return conversations

    model_name = seed_dataset_model() if dataset_model is None else dataset_model
    dataset_conversations = load_dataset_conversations(
        resolved_path,
        limit=limit,
        model_name=model_name,
        now=now - timedelta(days=1),
    )
    conversations.extend(dataset_conversations)
    return conversations


def _normalize_title(raw_title: str, *, fallback: str) -> str:
    cleaned = " ".join(raw_title.split())
    if not cleaned:
        cleaned = fallback
    return cleaned[:120]


def _normalize_turns(raw_turns: object) -> list[SeedTurn]:
    if not isinstance(raw_turns, list):
        return []

    turns: list[SeedTurn] = []
    for raw_turn in raw_turns:
        if not isinstance(raw_turn, dict):
            continue
        raw_role = str(raw_turn.get("role", "")).strip().lower()
        role = ROLE_MAP.get(raw_role)
        if role is None:
            continue
        text = " ".join(str(raw_turn.get("text", "")).split())
        if not text:
            continue
        turns.append(SeedTurn(role=role, content=text))

    return turns
