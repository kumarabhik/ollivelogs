# DESIGN.md — OlliveLogs

> Lightweight, multi-provider LLM inference logging & ingestion system, built to ace the Ollive Fullstack Engineer assignment and clear every Bonus item.

This is the **architecture contract**. If code disagrees with this doc, the doc wins until updated.

---

## 1. Goals

### 1.1 Primary (assignment-required)
1. A chatbot UI with multi-turn conversations + short-term context.
2. A lightweight SDK that wraps LLM calls and emits inference metadata in near real time.
3. An ingestion service that validates, enriches, and persists those events.
4. A schema that cleanly stores chat messages, inference logs, and extracted metadata.
5. A README with setup, architecture, schema decisions, tradeoffs, and "what I'd improve."

### 1.2 Bonus (guaranteed-interview)
- Multi-provider support (OpenAI, Anthropic, Gemini, DeepSeek, Grok, HuggingFace).
- Streaming responses (SSE end-to-end).
- Latency / throughput / errors dashboards.
- `docker compose up` one-command setup.
- Event-based architecture.
- PII redaction.
- Deploy on self-hosted k8s.

### 1.3 Stretch (what makes this top‑1)
- Two-SDK story: Python + TypeScript, both auto-instrument with one line.
- OpenTelemetry traces end-to-end (browser → chat-api → provider → ingest → DB).
- Hybrid OLTP+OLAP storage (Postgres + ClickHouse) so the dashboards stay fast at scale.
- Event-replay capability (Redis Streams consumer groups + DLQ).
- Conversation cancellation that actually aborts the upstream provider request, not just the UI.
- Cost tracking per provider/model with a live "$/conversation" widget.
- Eval harness baked in: replay logged conversations against a different model and diff outputs.

---

## 2. High-level architecture

```
┌──────────────┐        SSE        ┌──────────────┐
│  Next.js UI  │ ◄──────────────── │  chat-api    │
│  (browser)   │ ─── POST /chat ──►│  (FastAPI)   │
└──────┬───────┘                   └──────┬───────┘
       │                                  │ (1) call provider via SDK
       │                                  ▼
       │                          ┌───────────────┐
       │                          │  Provider     │
       │                          │  (OpenAI/…)   │
       │                          └───────┬───────┘
       │                                  │
       │             (2) SDK emits log    ▼
       │                          ┌───────────────┐
       │                          │ Redis Streams │
       │                          │  (event bus)  │
       │                          └───────┬───────┘
       │                                  │
       │            (3) consumer pulls    ▼
       │                          ┌───────────────┐
       │                          │ log-consumer  │
       │                          │  (worker)     │
       │                          └───┬─────┬─────┘
       │                              │     │
       │       chat msgs, convos      │     │  inference logs
       │             ▼                ▼     ▼
       │     ┌──────────────┐    ┌──────────────┐
       └────►│  Postgres    │    │  ClickHouse  │
       list/  │  (OLTP)      │    │  (OLAP)      │
       resume └──────────────┘    └──────┬───────┘
                                         │
                                         ▼
                                 ┌──────────────┐
                                 │  Grafana     │
                                 │  dashboards  │
                                 └──────────────┘
```

### 2.1 Why this shape
- **Two databases** because a single Postgres is fine for chat state but slow for "p99 latency per model in the last 24h over 10M rows." ClickHouse buys us that for free.
- **Redis Streams** because it's already in the stack (cache + sessions), supports consumer groups, ACKs, and replay, and avoids a Kafka cluster. The bus is wrapped behind an `EventBus` interface so swapping to Kafka is a config flip.
- **SDK emits to ingest, not direct DB writes.** That separation is the whole point of the assignment — fan‑in from many app instances to one ingestion pipeline.

---

## 3. Components

### 3.1 `apps/web` — Next.js 14 frontend
- App Router, React Server Components where possible.
- Chat page: textarea, streaming bubbles, stop button, model picker, provider picker, "regenerate" button.
- Conversations sidebar: list / resume / rename / delete / cancel.
- `/dashboard` page: embeds Grafana panels via iframe **or** renders the same data with Recharts (preferred — no iframe auth juggle).
- Auth: simple session cookie + Postgres `users` table. Anonymous play-mode is fine for the demo.

### 3.2 `apps/chat-api` — FastAPI
- `POST /v1/conversations` — create.
- `GET /v1/conversations` — list (paged, by user).
- `GET /v1/conversations/:id` — fetch with messages.
- `POST /v1/conversations/:id/messages` — send a user message, **streams** the assistant response over SSE.
- `POST /v1/conversations/:id/cancel` — flips a Redis flag the streaming loop checks every chunk → returns provider stream future cancelled.
- `DELETE /v1/conversations/:id` — soft delete.
- Uses `ollivelogs-py` SDK internally to wrap provider calls.
- Holds short-term context (last N=20 turns) inline; the full history is in Postgres.

### 3.3 `apps/ingest-api` — FastAPI
- `POST /v1/logs` — accepts a single log or a batch (gzip allowed).
- Validates with Pydantic v2 (strict).
- Idempotency: `event_id` (uuid v7) deduped via Redis `SET NX EX 24h`.
- **Pushes** the validated event to Redis Stream `logs.raw`. Does **not** write to DB inline — keeps p99 of the ingest call < 20ms.
- Health: `/healthz` (liveness), `/readyz` (Redis + Postgres ping).

### 3.4 `workers/log-consumer`
- Reads `logs.raw` with a consumer group `cg-store`.
- Pipeline (per event):
  1. PII redact (Presidio + regex pack) → produces `input_preview` (≤256 chars), `output_preview` (≤256 chars).
  2. Extract metadata (model, provider, tokens, latency, status, cost).
  3. Insert into Postgres `messages` (idempotent on `event_id`) and ClickHouse `inference_logs`.
  4. ACK. On exception → publish to `logs.dlq` with error reason.
- Configurable batch size (default 100) and flush interval (default 200ms).

### 3.5 `packages/ollivelogs-py`
- One-liner usage:
  ```python
  from ollivelogs import OlliveLogs
  ol = OlliveLogs(endpoint=os.getenv("OLLIVE_INGEST_URL"))

  with ol.trace(conversation_id=cid, user_id=uid) as span:
      resp = ol.wrap(openai_client).chat.completions.create(...)
      span.set_output(resp)
  ```
- `wrap()` returns a proxy that times the call, captures usage, status, and emits an event on `__exit__` (success or exception).
- Async batched HTTP client to ingest endpoint with retry + exponential backoff, capped queue, drop-oldest if overwhelmed (with a `dropped_events` counter).
- Zero hard deps on a specific provider — adapters live in `providers/`.

### 3.6 `packages/ollivelogs-js`
- Same idea, fetch interceptor for the browser and Node:
  ```ts
  import { OlliveLogs } from "ollivelogs";
  const ol = new OlliveLogs({ endpoint: process.env.OLLIVE_INGEST_URL });
  const openai = ol.wrap(new OpenAI({ apiKey }));
  ```
- Uses `navigator.sendBeacon` on `unload` so logs survive tab close.

### 3.7 Provider adapters
Common interface:
```python
class Provider(Protocol):
    name: str
    def chat(self, messages, model, stream: bool, **kw) -> Iterator[Chunk] | Response: ...
    def count_tokens(self, text: str, model: str) -> int: ...
    def price(self, model: str, prompt_t: int, completion_t: int) -> float: ...
```
First-class: OpenAI, Anthropic, Gemini, DeepSeek, Grok, HuggingFace Inference API (text-generation).

---

## 4. Database schema

### 4.1 Postgres (OLTP) — `db/migrations/`

```sql
-- users
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         CITEXT UNIQUE,
  display_name  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- conversations
CREATE TABLE conversations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID REFERENCES users(id) ON DELETE CASCADE,
  title         TEXT,
  status        TEXT NOT NULL CHECK (status IN ('active','cancelled','archived')) DEFAULT 'active',
  model_default TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON conversations (user_id, updated_at DESC);

-- messages (only chat content + light metadata; heavy log lives in ClickHouse)
CREATE TABLE messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL CHECK (role IN ('system','user','assistant','tool')),
  content         TEXT NOT NULL,        -- redacted preview by default
  content_full_id UUID,                 -- nullable FK to messages_full when raw is kept
  token_count     INT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_id        UUID UNIQUE           -- idempotency anchor from SDK
);
CREATE INDEX ON messages (conversation_id, created_at);

-- optional encrypted raw content (off by default)
CREATE TABLE messages_full (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content_enc BYTEA NOT NULL            -- pgcrypto, key from env
);
```

### 4.2 ClickHouse (OLAP) — `infra/clickhouse/init.sql`

```sql
CREATE TABLE inference_logs (
  event_id           UUID,
  ts                 DateTime64(3),
  conversation_id    UUID,
  user_id            UUID,
  provider           LowCardinality(String),
  model              LowCardinality(String),
  status             LowCardinality(String),  -- ok, error, cancelled, timeout
  error_kind         LowCardinality(String),  -- '' if ok
  latency_ms         UInt32,
  ttft_ms            UInt32,                  -- time to first token (streaming)
  prompt_tokens      UInt32,
  completion_tokens  UInt32,
  total_tokens       UInt32,
  cost_usd           Float64,
  input_preview      String,                  -- redacted, ≤256
  output_preview     String,                  -- redacted, ≤256
  client             LowCardinality(String),  -- 'web', 'py-sdk', 'js-sdk'
  sdk_version        LowCardinality(String),
  extra              String                   -- JSON blob, schemaless escape hatch
) ENGINE = MergeTree
ORDER BY (ts, provider, model)
PARTITION BY toYYYYMM(ts);
```

### 4.3 Why split
- `messages` is the source of truth for "render the chat." Small, indexed by conversation.
- `inference_logs` is the source of truth for "p95 latency for `gpt-4.1` last hour by `error_kind`." Wide, append-only, columnar.

---

## 5. Event schema (SDK → ingest)

```jsonc
{
  "event_id": "0190f0d8-...",            // uuid v7
  "ts": "2026-05-22T12:34:56.123Z",
  "conversation_id": "...",
  "user_id": "...",
  "client": "py-sdk",
  "sdk_version": "0.1.0",
  "provider": "openai",
  "model": "gpt-4.1-mini",
  "request": {
    "messages_preview": "user: hello…",   // truncated, may still contain PII at ingest time
    "stream": true,
    "temperature": 0.7
  },
  "response": {
    "text_preview": "Hi! How can I…",
    "finish_reason": "stop"
  },
  "usage": { "prompt_tokens": 12, "completion_tokens": 24, "total_tokens": 36 },
  "timing": { "latency_ms": 742, "ttft_ms": 188 },
  "status": "ok",                         // ok | error | cancelled | timeout
  "error": null,
  "cost_usd": 0.000123
}
```

Validation: Pydantic v2 model in `apps/ingest-api/schemas/event.py`. Versioned (`event_schema_version: int`) so we can evolve without breaking old clients.

---

## 6. PII redaction

- Library: **Microsoft Presidio Analyzer + Anonymizer**.
- Built-in recognizers used: `EMAIL_ADDRESS`, `PHONE_NUMBER`, `CREDIT_CARD`, `IBAN_CODE`, `IP_ADDRESS`, `PERSON`, `LOCATION`, `US_SSN`.
- Custom regex pack for: Aadhaar, PAN, India phone, Indian bank account/IFSC (likely audience).
- Strategy:
  - At **ingest worker** (not at SDK) — keeps the SDK lightweight, lets us update redaction rules without redeploying every client.
  - Two passes: (1) analyze → spans, (2) anonymize → replace with `<EMAIL>` etc.
  - Store both: `*_preview` (redacted) and optionally `*_full` (encrypted) gated by `STORE_RAW=true`.
  - The Python and JavaScript SDKs intentionally emit request/response previews untouched; the worker is the single redaction boundary for consistency and rule agility.

---

## 7. Observability

- **OpenTelemetry** instrumentation in chat-api, ingest-api, worker. Trace `traceparent` propagated from browser.
- **Prometheus** scrapes `/metrics` on each service. Custom metrics:
  - `llm_request_total{provider,model,status}` (counter)
  - `llm_latency_ms{provider,model}` (histogram)
  - `llm_ttft_ms{provider,model}` (histogram)
  - `llm_tokens_total{provider,model,kind}` (counter, kind=prompt|completion)
  - `llm_cost_usd_total{provider,model}` (counter)
  - `ingest_events_total{status}` (counter, status=ok|dedup|dropped|dlq)
  - `worker_lag_seconds` (gauge)
- **Grafana** dashboards committed as JSON in `infra/grafana/`. Three boards:
  1. **Inference Health** — RPS, p50/p95/p99, error rate, TTFT, by provider/model.
  2. **Cost & Tokens** — $/min, tokens/min, top conversations by spend.
  3. **Ingestion Pipeline** — events in/out, consumer lag, DLQ size, dedup rate.
- **Loki** for app logs (single line per request).

---

## 8. Streaming

- Browser ↔ chat-api: **SSE** (`text/event-stream`). Easy through proxies, no WebSocket complexity.
- chat-api ↔ provider: provider-native streaming, normalized to a `Chunk` iterator.
- Cancellation: chat-api stores a `cancel:{conversation_id}` key in Redis (TTL 60s). The streaming loop checks before each provider chunk forward — if set, it (a) cancels the upstream HTTP request via `httpx.AsyncClient` and (b) closes the SSE stream with a final `event: cancelled` frame. The partial assistant message is still persisted (status=`cancelled`).

---

## 9. Failure handling

| Failure | Behavior |
|---|---|
| Provider 5xx / timeout | SDK retries 2× with jitter; final failure → status=`error`, logged with `error_kind`. UI shows toast. |
| Ingest endpoint down | SDK queues in-memory ring buffer (default 1k events), drops oldest with counter. On reconnect, flushes. |
| Worker crash | Redis Stream consumer group means another worker resumes from last ACK. At-least-once delivery; dedup by `event_id`. |
| Postgres down | Worker pauses, increments `worker_lag_seconds`. Events remain on the stream (capped at 7 days). |
| ClickHouse down | Worker still writes Postgres; ClickHouse writes go to a side queue and replay. |
| Bad payload | Ingest 422 with structured error; SDK does **not** retry malformed events. |
| Schema mismatch | `event_schema_version` mismatch → ingest accepts known fields, drops unknown, logs warning. |

---

## 10. Scaling considerations (what we'd do at 100× load)

- Move ingest to a dedicated NLB; horizontal-scale stateless `ingest-api` pods.
- Switch event bus from Redis Streams to Kafka (already abstracted behind `EventBus`).
- Shard ClickHouse by `cityHash64(conversation_id)`; add a Distributed table.
- Move PII redaction off the hot path: tag events on ingest, redact lazily for display via a view.
- Pre-aggregate dashboards into a `MaterializedView` (1‑min, 5‑min, 1‑hr buckets).
- Token counting on big payloads is expensive — push to worker, never block the chat path.

---

## 11. Deployment

### 11.1 Local: `docker compose up`
Brings up: postgres, redis, clickhouse, grafana, prometheus, loki, chat-api, ingest-api, log-consumer, web. Single `make dev` command.

### 11.2 Self-hosted k8s
- `infra/helm/ollivelogs` chart.
- `values.yaml` with sensible defaults for k3s on a single VM (~2 vCPU / 4GB).
- HPA on chat-api and ingest-api (CPU + custom metric `llm_request_total` rate).
- PVCs for postgres + clickhouse + grafana + loki.
- Ingress: Traefik (k3s default) with TLS via cert-manager.
- Demo deploy: a single VPS running k3s, public DNS record pointing at it.

---

## 12. Security

- Auth: cookie-based session (signed JWT, HttpOnly, SameSite=Lax). Anonymous mode allowed for demo.
- Rate limits: per-IP + per-user (sliding window, Redis). Defaults: 30 req/min, 2k tokens/min.
- CORS: explicit allowlist via env.
- Provider keys: only ever read inside chat-api at request time; never logged. Rotated via env.
- DB credentials: docker secret in compose, k8s Secret on cluster.
- CSP on the frontend, no inline scripts.

---

## 13. Testing strategy

- **Unit:** pure functions (PII redactor, cost calc, token counter, schema validators).
- **Integration:** chat-api + Postgres + Redis via testcontainers. ingest-api → stream → worker → Postgres + ClickHouse, end-to-end.
- **Contract:** SDK ↔ ingest schema, locked by snapshot tests.
- **E2E:** Playwright drives the UI: send a message, watch SSE stream, cancel mid-stream, verify the cancelled record landed in DB.
- **Load:** `k6` script in `tests/load/` — 500 concurrent chats, asserts p95 < 1.5s.

---

## 14. Datasets — what we'd want

Listed in order of usefulness; agents must wait for the human to drop these into `data/`.

| Need | Why | Suggested source |
|---|---|---|
| **PII redaction test corpus** | To validate Presidio + custom recognizers, especially Indian PII. | Synthetic: generate with Faker (`pip install faker faker-india`). Human can also drop a public set like `microsoft/presidio-research`. |
| **Conversation seed** | Realistic multi-turn chats for demo + load test. | Public: `Anthropic/hh-rlhf` (short turns), `LMSYS-Chat-1M` (large, filter). Or synthesize 50 turns with one of our providers. |
| **Eval prompts** | To run the eval harness (replay logs against another model and diff). | Public: `MMLU`, `MT-Bench`, or curated 50 prompts of our own. |
| **Cost reference table** | To populate `Provider.price()` correctly. | Hand-maintained `infra/pricing.yaml`, sourced from each provider's public pricing page. No dataset download needed. |

**Bottom line:** the only thing we *strictly* need a real dataset for is PII redaction evaluation, and even there synthetic data with Faker is enough to ship. Tell me if you want a public PII set fetched.

---

## 15. Provider keys (deferred)

When the human gives the go-ahead, pull HuggingFace tokens (and any others) from the `aegisdesk` project under XOXO. **Do not read aegisdesk now.** Drop the keys into `.env` (never commit). Document which providers are wired up in README.

---

## 16. Schema log (append-only)

| Date | Change | Migration | Author |
|---|---|---|---|
| 2026-05-22 | Initial schema (Postgres + ClickHouse) | `0001_init.sql` | Claude |

---

## 17. What we explicitly chose NOT to do (and why)

- **Not Kafka by default.** Overkill for the assignment scope; Redis Streams gives us the same semantics at 1/10 the ops weight. Interface is ready to swap.
- **Not a vector DB.** No RAG in scope. We'll keep the door open with a `embeddings` table if asked later.
- **Not a hosted observability SaaS.** Has to be self-hostable for the k8s bonus.
- **Not GraphQL.** REST + SSE keeps the SDK simple.
- **Not LangChain/LangGraph.** Adds a dependency surface we don't need; our provider abstraction is ~150 LOC.

---

## 18. README sections we still owe (for submission)

- Setup (one command).
- Architecture overview (link this doc + the PNG).
- Schema decisions (link §4).
- Tradeoffs (link §17).
- What I'd improve with more time (link §10 + a short list).
- Demo: Loom + screenshots in `docs/demo/`.
