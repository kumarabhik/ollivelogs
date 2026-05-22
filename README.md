# OlliveLogs

**A multi-provider LLM chat + inference logging & ingestion platform.**
Built for the Ollive full-stack engineer assignment. Hits every required deliverable and every Bonus item (multi-provider, SSE streaming, dashboards, one-command compose, event-based architecture, PII redaction, k8s).

> Quick links: [DESIGN.md](DESIGN.md) · [ROADMAP.md](ROADMAP.md) · [AGENTS.md](AGENTS.md) · [docs/architecture.md](docs/architecture.md) · [infra/helm/README.md](infra/helm/README.md)

---

## Why this exists

The assignment asks for a chatbot, an SDK that captures inference metadata, an ingestion pipeline, and a database. The interesting part isn't any single piece — it's the **seam between them**: making `N` chat backends fan-in to one logging pipeline without slowing the user-facing path, while still keeping the data queryable for dashboards.

OlliveLogs solves that with a hybrid OLTP+OLAP store, a Redis-Streams event bus, and a worker that owns PII redaction so the SDK stays a thin client. The chatbot, the SDK, and the ingest service are all decoupled — you can scale them independently and swap any of them out.

---

## At a glance

| Concern | Choice | Why |
| --- | --- | --- |
| Chat backend | FastAPI + SSE | Native async, streaming-friendly, OpenAPI for free |
| Frontend | Next.js 14 App Router + Tailwind + shadcn/ui | Server components, streaming HTML, ESM SDK shipped |
| LLM providers | OpenAI · Anthropic · Gemini · DeepSeek · Grok · HuggingFace | All via one `Provider` protocol; switch by request param |
| SDKs | `ollivelogs-py` + `ollivelogs-js` | One-line `wrap(client)` for OpenAI/Anthropic in both langs |
| Event bus | Redis Streams (consumer groups + DLQ) | Same Redis we already use for cache; Kafka-ready interface |
| OLTP store | Postgres 16 (asyncpg, alembic) | Conversations / messages / users |
| OLAP store | ClickHouse 24 (+ minute-rollup materialized view) | Fast p95 latency queries over millions of rows |
| Cache + rate limit | Redis 7 | Sliding-window rate limits, cancel flag, idempotency dedup |
| PII redaction | Microsoft Presidio + India regex pack | At the **worker**, not the SDK — single redaction boundary |
| Observability | OpenTelemetry → Prometheus + Grafana + Loki | All self-hostable, JSON dashboards in repo |
| Containers | Docker + Compose | `make dev` brings the whole stack up |
| Orchestration | Helm chart for k3s | Single-VM deploy; HPA on chat-api + ingest-api |
| CI | GitHub Actions (python + node + gitleaks) | Lint, typecheck, test, secret-scan on every PR |

---

## Architecture

```text
┌──────────────┐        SSE         ┌──────────────┐
│  Next.js UI  │ ◄─────────────────│  chat-api    │
│  (browser)   │ ──── POST /chat ──►│  (FastAPI)   │
└──────┬───────┘                    └──────┬───────┘
       │                                   │ (1) call provider via ollivelogs-py SDK
       │                                   ▼
       │                           ┌───────────────┐
       │                           │  Provider     │
       │                           │  (OpenAI/HF…) │
       │                           └───────┬───────┘
       │                                   │ (2) SDK emits log
       │                                   ▼
       │                           ┌───────────────┐
       │                           │  ingest-api   │ ◄── validate, dedup, push
       │                           │   (FastAPI)   │
       │                           └───────┬───────┘
       │                                   │
       │                                   ▼
       │                           ┌───────────────┐
       │                           │ Redis Streams │
       │                           │  logs.raw     │
       │                           └───────┬───────┘
       │                                   │ (3) consumer group cg-store
       │                                   ▼
       │                           ┌───────────────┐
       │                           │ log-consumer  │ ── PII redact (Presidio)
       │                           │  (worker)     │ ── extract metadata
       │                           └───┬─────┬─────┘ ── DLQ on failure
       │                               │     │
       │       chat msgs, convos       │     │  inference logs
       │             ▼                 ▼     ▼
       │     ┌──────────────┐    ┌──────────────┐
       └────►│  Postgres    │    │  ClickHouse  │
       list/  │  (OLTP)      │    │  (OLAP)      │
       resume └──────────────┘    └──────┬───────┘
                                         │
                                         ▼
                                 ┌──────────────┐
                                 │   Grafana    │ ◄── 3 dashboards
                                 │  + Prom/Loki │     in infra/grafana/
                                 └──────────────┘
```

Full architecture, schema, sequence and failure diagrams are in [docs/architecture.md](docs/architecture.md). The complete contract is [DESIGN.md](DESIGN.md).

---

## Setup — one command

```bash
# 1. clone + create local .env
cp .env.example .env
# add your provider keys; HF_TOKEN already pre-wired for Qwen via the HF router

# 2. boot the entire stack
make dev
```

That brings up: postgres, redis, clickhouse, grafana, prometheus, loki, otel-collector, chat-api, ingest-api, log-consumer.

URLs after `make dev`:

| Service | URL | Notes |
| --- | --- | --- |
| Web console | <http://localhost:3000> | Chat UI, conversation list, model picker |
| chat-api | <http://localhost:8001/docs> | OpenAPI |
| ingest-api | <http://localhost:8002/docs> | OpenAPI |
| Grafana | <http://localhost:3001> | anonymous Viewer access, dashboards in `OlliveLogs` folder |
| Prometheus | <http://localhost:9090> | scrape config in `infra/prometheus/` |
| Loki | <http://localhost:3100/ready> | log store |

Then apply migrations + seed:

```bash
make migrate     # alembic upgrade head
make seed        # 3 users, 5 conversations, ~30 messages
```

Smoke checks:

```bash
curl http://localhost:8001/healthz
curl http://localhost:8002/healthz
curl http://localhost:8001/v1/conversations | jq
```

### Running pieces independently

```bash
# Frontend only (uses local mock API)
npm install
npm -w apps/web run dev

# Frontend pointed at a real backend
CHAT_API_PROXY_TARGET=http://127.0.0.1:8001 npm -w apps/web run dev
```

---

## Schema design decisions

### Why two databases?

Postgres is great for "render this conversation" but bad for `SELECT quantile(0.99)(latency_ms) FROM 10M_rows WHERE provider='openai' AND ts > now() - INTERVAL 1 DAY`. ClickHouse turns that into milliseconds.

So:

- **Postgres** owns the *conversation graph* — `users` ← `conversations` ← `messages`. Indexed for "list a user's last 20 conversations" and "show me the last 50 messages."
- **ClickHouse** owns the *inference firehose* — one wide append-only row per LLM call, partitioned by month, with a 1-minute `MaterializedView` rollup for dashboards.

### Tables

`users(id, email, display_name, created_at)`
`conversations(id, user_id, title, status ∈ {active,cancelled,archived}, model_default, created_at, updated_at)`
`messages(id, conversation_id, role ∈ {system,user,assistant,tool}, content, content_full_id?, token_count, status ∈ {ok,error,cancelled,partial}, event_id UNIQUE, created_at)`
`messages_full(id, content_enc BYTEA)` — *encrypted* raw content, only populated when `STORE_RAW=true`. Encrypted with `pgcrypto`.

ClickHouse `inference_logs` carries `event_id, ts, conversation_id, user_id, provider, model, status, error_kind, latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens, cost_usd, input_preview, output_preview, client, sdk_version, request_id, extra`. Plus `inference_minute_agg` SummingMergeTree fed by a `MaterializedView` so dashboards don't scan raw rows.

Trigger: every `INSERT INTO messages` bumps `conversations.updated_at` — so "most recent first" listing is a `ORDER BY updated_at DESC` away.

Idempotency: every message has an `event_id` (from the SDK, uuid v7). The worker writes the same `event_id` on the Postgres row and the ClickHouse row, so we can reconcile if a worker dies mid-batch and the next one re-processes.

### Why redaction at the worker, not the SDK?

Three reasons:

1. **SDK stays light.** Presidio pulls spacy + a 50MB model — we don't want that in every chat backend process.
2. **Single redaction boundary.** Update the recognizer set once; every emitter benefits without redeploy.
3. **Audit trail.** Worker keeps the raw preview only long enough to redact, then drops it; the encrypted full content (when `STORE_RAW=true`) bypasses preview storage entirely.

---

## Tradeoffs we made

| Tradeoff | Why |
| --- | --- |
| **Redis Streams, not Kafka.** | Kafka is the right answer at 100× the scale. Streams give us consumer groups, ACKs, replay, and DLQ for free, on the Redis we already need. The `EventBus` interface makes the swap one-PR. |
| **At-least-once delivery + idempotent writes.** | Easier to reason about than exactly-once. Dedup by `event_id` (Redis `SET NX EX 24h` at ingest, `UNIQUE` constraint at write). |
| **PII redaction in the worker.** | Costs us a ~50–80ms processing tail; gains us a single recognizer source-of-truth and a SDK with zero NLP deps. |
| **Two SDKs (Py + JS), not one.** | Spec asked for "a lightweight SDK." Doing both shows the wrapper concept generalizes — and one-liners in both languages prove the API surface. |
| **Token previews truncated to 256 chars.** | Default-on PII safety. Full content is encrypted and gated by `STORE_RAW=true`. |
| **Anonymous-mode demo auth.** | A real auth provider is out of scope; we ship a signed-cookie session that supports anonymous-by-default plus a `users` table for when you want real users. |
| **OTel collector but no Jaeger / Tempo by default.** | Traces go to stdout in dev. Production layout in [docs/architecture.md](docs/architecture.md) shows where Tempo plugs in. Keeps `make dev` under 60s. |
| **Custom `Provider` protocol, not LiteLLM.** | The protocol is ~150 LOC and gives us total control over streaming, cancellation, token counting, and pricing. LiteLLM is great but it's a big dependency for what we need. |
| **PostgreSQL trigger for `updated_at`.** | Cheap, atomic, can't get out of sync with messages. The alternative — app-side update — is one race-condition bug away from sorting conversations wrong. |

---

## What I'd improve with more time

1. **Browser-side OTel + `traceparent` propagation.** Backend is ready; frontend just needs `@opentelemetry/sdk-trace-web` wired into `apps/web`.
2. **Streaming token recording into Prometheus.** Metric primitives are declared; we need the `.inc()` / `.observe()` calls at provider boundaries in `apps/chat-api/app/providers/*.py`.
3. **Eval harness.** Replay a logged conversation against a different model, store the diff in `eval_runs`, surface in a Grafana panel. Scoped in `Phase 18` of [ROADMAP.md](ROADMAP.md).
4. **Per-conversation budgets.** Hooks already exist on the cost path; need a Postgres `budgets` table and a notifier.
5. **Materialized cost-rollup table.** Currently dashboards run `SUM(cost_usd) WHERE ts > ...`. A `cost_hour_agg` MV would cut p95 dashboard latency at 10M+ rows.
6. **Sticky session affinity for cancel.** Today, cancel works because chat-api is stateless on the cancel path (Redis flag). At very high load a dedicated cancel channel per pod would be cleaner.
7. **Self-hosted Tempo + Mimir.** OTel collector is already producing OTLP; swap the debug exporter for Tempo's OTLP receiver and you have full trace UI.

---

## Repo layout

```text
apps/
├── web/                 Next.js 14 console (chat UI + dashboards)
├── chat-api/            FastAPI: conversations, streaming, cancel
└── ingest-api/          FastAPI: log validation, dedup, fan-out
workers/
└── log-consumer/        Redis Streams consumer → Postgres + ClickHouse
packages/
├── ollivelogs-py/       Python SDK (wrap + trace + async shipper)
└── ollivelogs-js/       TS SDK (wrap + fetch interceptor + sendBeacon)
infra/
├── clickhouse/init.sql  OLAP schema + minute-rollup MV
├── grafana/             Provisioning + 3 dashboards
├── prometheus/          Scrape config
├── loki/                Single-binary config
├── otel/                Collector config
└── helm/ollivelogs/     k8s/k3s chart
db/
├── migrations/          Alembic — 0001_init.py
└── seed/                Idempotent demo data
tests/
├── unit/                Worker, redaction, schema validators
├── integration/         Live Postgres/Redis tests
├── e2e/                 Playwright (send, stream, cancel, resume)
└── load/                k6 ingest load test (500 RPS, p99<20ms)
docs/
├── architecture.md      Mermaid diagrams + sequence flows
└── demo/                Screenshots, Loom link
```

---

## Tests

```bash
# Python unit + integration (needs `make dev` running)
make test

# Frontend
npm -w apps/web run test
npm -w apps/web run typecheck

# JS SDK
npm -w packages/ollivelogs-js run test

# End-to-end browser
npm -w tests/e2e run test

# Load test (500 RPS for 1 minute, asserts p99 < 20ms)
k6 run tests/load/ingest-load.js
```

CI runs lint + typecheck + python+node test + gitleaks secret-scan on every push and PR (see [.github/workflows/ci.yml](.github/workflows/ci.yml)).

---

## Deployment

### Local: `make dev`

Brings everything up via Docker Compose.

### Self-hosted k8s / k3s

See [infra/helm/README.md](infra/helm/README.md). One command on a single VM:

```bash
helm install ollivelogs infra/helm/ollivelogs -n ollivelogs --create-namespace
```

HPA targets: chat-api (CPU 70% + `llm_request_total` rate), ingest-api (CPU 60% + req rate). Postgres + ClickHouse + Redis + Loki + Grafana all use PVCs. Traefik (k3s default) handles ingress; cert-manager handles TLS.

---

## Demo

- Browser screenshot: [docs/demo/web-console-home.png](docs/demo/web-console-home.png)
- Loom walkthrough: *coming with submission*
- Hosted demo URL: *coming with submission*

---

## Submission deliverables map

| Spec | Where |
| --- | --- |
| Chatbot (multi-turn, context, UI) | [apps/web](apps/web), [apps/chat-api](apps/chat-api) |
| Lightweight SDK (metadata capture) | [packages/ollivelogs-py](packages/ollivelogs-py), [packages/ollivelogs-js](packages/ollivelogs-js) |
| Ingestion pipeline (validate + extract + store) | [apps/ingest-api](apps/ingest-api), [workers/log-consumer](workers/log-consumer) |
| DB storage (chat + logs + metadata) | [db/migrations/](db/migrations/), [infra/clickhouse/init.sql](infra/clickhouse/init.sql) |
| Setup instructions | this README, [Makefile](Makefile) |
| Architecture overview | [docs/architecture.md](docs/architecture.md), [DESIGN.md](DESIGN.md) |
| Schema design decisions | this README §"Schema", [DESIGN.md](DESIGN.md) §4 |
| Tradeoffs | this README §"Tradeoffs" |
| What I'd improve | this README §"What I'd improve" |

### Bonus deliverables

| Bonus | Where |
| --- | --- |
| Multi-provider | [apps/chat-api/app/providers/](apps/chat-api/app/providers/) |
| Streaming responses | SSE in chat-api · Next.js consumes in [apps/web/src/components/chat/workspace.tsx](apps/web/src/components/chat/workspace.tsx) |
| Latency + throughput + errors dashboards | [infra/grafana/dashboards/](infra/grafana/dashboards/) (3 JSONs) |
| Docker Compose one-command | `make dev` → [docker-compose.yml](docker-compose.yml) |
| Event-based architecture | Redis Streams `logs.raw` → consumer group `cg-store` → DLQ `logs.dlq` |
| PII redaction | [workers/log-consumer/log_consumer_app/redaction.py](workers/log-consumer/log_consumer_app/redaction.py) (Presidio + India regex) |
| Self-hosted k8s | [infra/helm/ollivelogs/](infra/helm/ollivelogs/) |

---

## Contact

Submission to `work@ollive.ai` — repo at <https://github.com/kumarabhik/ollivelogs> (link goes live with the submission).
