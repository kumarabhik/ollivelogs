# AGENTS.md — Operating Contract for Agents on this Repo

This file is the single source of truth for **how agents (Claude, Cursor, Copilot, etc.) must behave** inside this project. Anything not in this file is up for judgement; anything *in* this file is binding.

Project codename: **OlliveLogs** (working name — Ollive Fullstack Engineer Assignment).
Goal: Build the most advanced lightweight LLM inference logging + ingestion system possible within the assignment spec, hit every Bonus item, and ship a top‑1% submission.

---

## 0. Golden Rules (non‑negotiable)

1. **Read [DESIGN.md](DESIGN.md) before writing code.** That is the architecture contract.
2. **Update [SYSTEM.md](SYSTEM.md) after every meaningful action.** Append-only log, newest entry at top, ISO‑8601 timestamp, what changed and why.
3. **Update [ROADMAP.md](ROADMAP.md) checkboxes** the moment a task moves between states: `[ ]` → `[~]` → `[x]`. Never lie about state.
4. **No secrets in the repo.** All API keys (OpenAI, Anthropic, Gemini, HuggingFace, Grok, DeepSeek) live in `.env` (gitignored). Use `.env.example` as the template.
5. **Never delete user data on disk** without explicit confirmation in chat.
6. **Match scope.** Build what's in the roadmap. Don't refactor adjacent code "while you're there."
7. **One PR / one concern.** If you discover unrelated bugs, file them in ROADMAP under `Backlog`, don't fix inline.
8. **Ask before destructive ops:** `rm -rf`, `git reset --hard`, dropping DB tables, force‑pushing, deleting branches.

---

## 1. Tech Stack — Locked

Do not switch any of these without updating [DESIGN.md](DESIGN.md) first.

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui | Modern, streaming-friendly, server components |
| Chat backend | FastAPI (Python 3.11) | Async, SSE-native, fast iteration |
| LLM abstraction | Custom `providers/` adapters + `litellm` fallback | Multi-provider, swap with env var |
| SDK (Python) | `ollivelogs-py/` — thin wrapper, decorator + context manager | Auto-capture latency/tokens/errors |
| SDK (TS) | `ollivelogs-js/` — fetch/axios interceptor + ESM | First-class browser + Node |
| Event bus | Redis Streams (primary), Kafka-ready interface | Lightweight default, scales out |
| Ingestion worker | FastAPI consumer + async batch writer | Backpressure-aware |
| OLTP DB | PostgreSQL 16 | Conversations, messages, users, sessions |
| OLAP DB | ClickHouse | Inference logs, time-series, dashboards |
| Cache / session | Redis 7 | Conversation context, rate-limit, idempotency |
| PII redaction | Microsoft Presidio + custom regex pack | Pluggable analyzer/anonymizer |
| Observability | OpenTelemetry → Prometheus + Grafana + Loki | Single pane of glass |
| Container | Docker + Docker Compose v2 | `docker compose up` = full stack |
| Orchestration | Kubernetes + Helm + (optionally) k3s for self-hosted | Bonus deliverable |
| CI/CD | GitHub Actions | Lint, type-check, test, build, push images |
| Tests | pytest, vitest, playwright (e2e) | Pyramid |

---

## 2. Repo Layout (canonical)

```
FullStackAssignment/
├── AGENTS.md                # this file
├── SYSTEM.md                # agent step log
├── DESIGN.md                # architecture & spec
├── ROADMAP.md               # progress tracker
├── README.md                # submission-facing
├── .env.example
├── docker-compose.yml
├── docker-compose.prod.yml
├── Makefile                 # one-word commands (make dev, make test, make seed)
│
├── apps/
│   ├── web/                 # Next.js frontend (chat UI + dashboards)
│   ├── chat-api/            # FastAPI: chat, streaming, conversation CRUD
│   └── ingest-api/          # FastAPI: log ingestion, validation, fan-out
│
├── workers/
│   └── log-consumer/        # Redis Streams consumer → Postgres + ClickHouse
│
├── packages/
│   ├── ollivelogs-py/       # Python SDK
│   └── ollivelogs-js/       # TS SDK
│
├── infra/
│   ├── k8s/                 # raw manifests
│   ├── helm/                # helm chart
│   ├── grafana/             # dashboards as JSON
│   ├── prometheus/          # scrape configs
│   └── clickhouse/          # init schemas
│
├── db/
│   ├── migrations/          # alembic for Postgres
│   └── seed/                # seed scripts + fixtures
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
└── docs/
    ├── architecture.png
    ├── schema.png
    └── demo/                # screenshots, gifs, loom link
```

---

## 3. Workflow Contract for Every Agent Session

Every time an agent picks up work:

1. **Open SYSTEM.md.** Read the last 5 entries.
2. **Open ROADMAP.md.** Pick the next `[ ]` (or resume a `[~]`).
3. **Mark it `[~]`** with your initials/agent name and date.
4. **Do the work.** Small commits, conventional commit messages.
5. **Append a SYSTEM.md entry** describing: what, why, files touched, follow-ups.
6. **Flip the box to `[x]`** only if the task is end-to-end verifiable (tests pass, feature visible).
7. **If blocked:** leave it `[~]`, add a `Blocked:` note in SYSTEM.md with the exact question.

---

## 4. Coding Rules

- **Python:** ruff + black + mypy strict on `apps/` and `packages/ollivelogs-py/`. No `Any` in public signatures.
- **TypeScript:** strict mode on. No `any` in exported types. ESLint + Prettier.
- **Tests:** every new endpoint ships with at least one happy-path test + one failure test. Coverage target: 70% for `apps/`, 90% for SDKs.
- **Commit style:** `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`. Scope optional.
- **No console.log / print in committed code.** Use the logger.
- **Schema changes** must come with an Alembic migration AND a one-line note in DESIGN.md under "Schema log."

---

## 5. Security & Privacy

- PII redaction is **on by default** in the ingestion pipeline. Raw input/output is stored only as **preview (first 256 chars, redacted)**; full content goes to a separate `messages_full` table that is encrypted at rest (pgcrypto) and gated by a feature flag.
- API keys: never log them, never echo them, never commit them. CI has a secret scanner step.
- Tokens for HuggingFace and other providers will be provided later — see [DESIGN.md](DESIGN.md) §"Provider keys."

---

## 6. What Agents Must NOT Do

- ❌ Don't add new top-level frameworks (e.g., switch FastAPI → Flask) without updating DESIGN.md + getting human OK.
- ❌ Don't introduce paid SaaS dependencies (Datadog, LaunchDarkly) — must be self-hostable.
- ❌ Don't write features not on the roadmap.
- ❌ Don't mock the database in integration tests — use a real Postgres/Redis via testcontainers.
- ❌ Don't squash someone else's work; rebase carefully or stop and ask.
- ❌ Don't push directly to `main`. PRs only.
- ❌ Don't claim a task is done without a SYSTEM.md entry and a green CI run.

---

## 7. Datasets

Agents do **not** download datasets autonomously. If a task needs one, add it to ROADMAP under `Datasets needed` and stop. The human owner will fetch and place under `data/` (gitignored). Current expected needs are listed at the bottom of [DESIGN.md](DESIGN.md).

---

## 8. Provider Keys

API/HuggingFace tokens for advanced features will be provided later by the human (source: `aegisdesk` in XOXO — **do not read it yet, wait for explicit instruction**). Until then, agents code against env vars with sensible defaults and stub clients in tests.

---

## 9. Definition of "Done" for the Whole Project (Top‑1 bar)

A task on ROADMAP is `[x]` *only if* all are true for that task:

- Code merged on the target branch.
- Unit + integration tests pass locally and in CI.
- For UI tasks: verified manually in the browser, screenshot in `docs/demo/`.
- For infra tasks: `docker compose up` (or `helm install`) reproduces the result on a clean machine.
- SYSTEM.md entry written.
- DESIGN.md updated if architecture shifted.
- README.md updated if the user-facing surface changed.
