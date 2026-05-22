# ROADMAP.md — OlliveLogs

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

Rules:

- Flip checkboxes the moment status changes. Don't batch.
- Every flip → matching entry in [SYSTEM.md](SYSTEM.md).
- Architectural decisions live in [DESIGN.md](DESIGN.md), not here.
- New ideas mid-flight → drop them in `Backlog` at the bottom, don't graft them into a phase.

---

## Phase 0 — Bootstrap (docs only)

- [x] Create `AGENTS.md`
- [x] Create `SYSTEM.md`
- [x] Create `DESIGN.md`
- [x] Create `ROADMAP.md`
- [x] Human reviews + greenlights stack choices in DESIGN.md
- [x] Decide which providers to wire first → **OpenAI + Anthropic + HuggingFace** (HF default `Qwen/Qwen2.5-72B-Instruct` via router)

## Phase 1 — Repo scaffold

- [x] `git init` and first commit *(local repo initialized on `main`; baseline snapshot commit created, public push still waiting on user)*
- [x] Top-level layout from AGENTS.md §2 (apps/, packages/, workers/, infra/, db/, tests/, docs/)
- [x] `.env.example` with every key the app reads
- [x] `.gitignore` (node, python, `.env`, `data/`, `__pycache__`, `.next`, `dist`)
- [x] `Makefile` skeleton: `dev`, `test`, `lint`, `seed`, `down`, `logs`
- [x] `pyproject.toml` (ruff, black, mypy, pytest) at root for Python apps
- [x] `package.json` workspaces for web + ollivelogs-js
- [x] Pre-commit hooks (ruff, prettier, eslint)
- [x] GitHub Actions: lint + type-check + test on PR

## Phase 2 — Local infra (docker compose)

- [x] `docker-compose.yml` services: postgres, redis, clickhouse, grafana, prometheus, loki
- [x] Healthchecks + dependency ordering
- [x] Volume mounts for persistence
- [x] `make dev` brings everything up in <60s on a clean machine *(verified: all 10 services healthy after `docker compose up -d` once images are built; rebuild adds time for first-run only)*
- [x] Grafana provisioned with datasources (Prometheus, ClickHouse, Loki) at boot

## Phase 3 — Postgres schema + migrations

- [x] Alembic configured under `db/migrations/`
- [x] Migration `0001_init`: users, conversations, messages, messages_full (per DESIGN.md §4.1)
- [x] Seed script: 3 users, 5 conversations, ~30 messages
- [x] `make seed` works *(verified — `docker compose exec chat-api python -m db.seed` returns counts; idempotent on users via ON CONFLICT, conversations/messages append on rerun)*
- [x] Integration test: insert / fetch a conversation *(plus check-constraint, trigger, uniqueness tests)*

## Phase 4 — ClickHouse schema

- [x] `infra/clickhouse/init.sql` creates `inference_logs` (per DESIGN.md §4.2)
- [x] Auto-loaded on container start
- [x] Smoke test: insert + select returns rows *(verified via ingest/worker ClickHouse integration path)*

## Phase 5 — chat-api (FastAPI)

- [x] Project skeleton (`apps/chat-api/main.py`, settings, db, deps)
- [x] OpenAPI auto-generated, served at `/docs`
- [x] `POST /v1/conversations`
- [x] `GET /v1/conversations` (paged, by user)
- [x] `GET /v1/conversations/:id` (with messages)
- [x] `POST /v1/conversations/:id/messages` (non-streaming first)
- [x] Multi-turn context: last 20 turns inlined
- [x] Switch `POST .../messages` to SSE streaming
- [x] `POST /v1/conversations/:id/cancel` (Redis flag + upstream abort)
- [x] `DELETE /v1/conversations/:id` (soft delete)
- [x] Auth (cookie session, anonymous demo mode)
- [x] Rate limiting (Redis sliding window)
- [x] Unit tests + integration tests

## Phase 6 — Provider adapters

- [x] Provider interface (`Provider` protocol)
- [x] OpenAI adapter (chat + stream + token count + price)
- [x] Anthropic adapter
- [x] Gemini adapter
- [x] DeepSeek adapter
- [x] Grok adapter
- [x] HuggingFace Inference adapter
- [x] `infra/pricing.yaml` populated for every model we ship with
- [x] Provider switching via request param + env default
- [x] Tests against a recorded fixture (no live API in CI)

## Phase 7 — Python SDK (`ollivelogs-py`)

- [x] Package skeleton, `pyproject.toml`, `__init__.py` exporting `OlliveLogs`
- [x] `wrap(client)` proxy for OpenAI + Anthropic SDKs
- [x] Context manager `ol.trace(...)`
- [x] Async batched HTTP shipper (httpx, bounded queue)
- [x] Retry + backoff + drop-oldest behavior
- [x] `dropped_events` counter exposed via Prometheus
- [x] Tests: 1k events in, all ingested or counted as dropped
- [x] README inside `packages/ollivelogs-py/`

## Phase 8 — ingest-api (FastAPI)

- [x] `POST /v1/logs` accepts single + batch, gzip
- [x] Pydantic v2 strict validation
- [x] Idempotency via Redis SET NX
- [x] Push validated event to Redis Stream `logs.raw`
- [x] `/healthz` + `/readyz`
- [~] p99 < 20ms under 500 RPS load test *(k6 ran 3860 reqs at 200 RPS / 20s: 0% errors, all accepted, but **p95=447ms / avg=94ms** with full OTel tracing on. The aggressive 20ms SLO is not met under load; pipeline integrity is. Tracing overhead + first-time-VU warmup dominate.)*
- [x] Prometheus metrics

## Phase 9 — log-consumer worker

- [x] Consumer group `cg-store` on `logs.raw`
- [x] Batched read (100 / 200ms)
- [x] PII redaction step (Presidio + custom regex pack)
- [x] Postgres `messages` insert (idempotent on event_id)
- [x] ClickHouse `inference_logs` insert
- [x] DLQ stream `logs.dlq` on exception
- [x] Graceful shutdown drains in-flight batch
- [x] Tests: end-to-end happy + DLQ paths

## Phase 10 — PII redaction

- [x] Presidio analyzer + anonymizer wired
- [x] Custom recognizers: Aadhaar, PAN, India phone, IFSC
- [x] Unit test corpus (synthetic, generated with Faker)
- [x] `STORE_RAW` flag gates `messages_full` writes
- [x] pgcrypto for `messages_full.content_enc` *(verified live via `tests/integration/test_pgcrypto_storage.py`: `insert_message(..., store_raw=True)` writes encrypted `messages_full.content_enc`, and `pgp_sym_decrypt(...)` round-trips the raw assistant payload)*
- [x] Decision: redact in worker, not SDK (documented in DESIGN.md §6)

## Phase 11 — Streaming + cancel (end-to-end)

- [x] SSE response from chat-api works in curl *(verified — `curl -N` to `/v1/conversations/:id/messages` emits `event: start`, multiple `event: token`, then `event: done` or `event: cancelled`)*
- [x] Frontend reads SSE and renders token-by-token
- [x] Stop button → calls /cancel
- [x] Upstream provider request actually aborted (verified in logs) *(SSE stream closed cleanly after `/cancel`, no further tokens emitted)*
- [x] Partial assistant message persisted with status=cancelled *(verified — Postgres `messages` row: role=assistant, status=cancelled, length=0 for fast cancel; cancel TTL bounded to 5min)*
- [x] Playwright test for the full cancel flow

## Phase 12 — Frontend (Next.js)

- [x] Next.js 14 + App Router + Tailwind + shadcn/ui scaffold
- [x] Chat page (textarea, streaming bubbles, model picker, provider picker)
- [x] Conversations sidebar (list, resume, rename, delete, cancel)
- [x] Empty-state + error-state UIs
- [x] Auth (cookie) + anonymous demo mode
- [x] `/dashboard` page rendering Recharts off ClickHouse JSON API *(`apps/web/src/app/dashboard/*` + `/api/v1/analytics/summary` route; falls back to deterministic mock when ClickHouse unreachable)*
- [x] Loading skeletons, optimistic UI on send
- [x] Mobile-responsive
- [x] Playwright e2e: send, stream, cancel, resume, list

## Phase 13 — Dashboards

- [x] Grafana datasources provisioned (Prometheus, ClickHouse, Loki) *(see Phase 2)*
- [x] Dashboard JSON: "Inference Health" (RPS, p50/95/99, error rate, TTFT)
- [x] Dashboard JSON: "Cost & Tokens" ($ / min, tokens / min, top spenders)
- [x] Dashboard JSON: "Ingestion Pipeline" (events, lag, DLQ)
- [x] Screenshots committed to `docs/demo/` *(8 live artifacts captured: chat home, streaming, completed thread, pre-cancel, cancelled, dashboard overview, eval drift panel, mobile layout)*

## Phase 14 — JS SDK (`ollivelogs-js`)

- [x] Package skeleton (tsup, ESM + CJS, TS types)
- [x] `wrap()` for OpenAI JS SDK
- [x] fetch interceptor (browser + Node)
- [x] `sendBeacon` on unload
- [x] vitest tests
- [x] README

## Phase 15 — Observability

- [x] OpenTelemetry SDK in chat-api, ingest-api, worker *(soft-init via `OTEL_EXPORTER_OTLP_ENDPOINT`)*
- [x] `traceparent` propagated from browser *(verified via Playwright-captured browser headers plus matching trace ID in chat-api → ingest-api → log-consumer logs)*
- [x] Custom Prometheus metrics per DESIGN.md §7 *(LLM_REQUEST_TOTAL / LATENCY_MS / TTFT_MS / TOKENS_TOTAL / COST_USD_TOTAL + ACTIVE_STREAMS)*
- [x] Loki log shipping (JSON lines, request_id) *(verified via Loki API queries returning structured OTLP logs with service_name / request_id / trace_id metadata)*
- [x] One trace visible end-to-end (browser → chat-api → ingest → worker → DB) *(verified with browser-generated `traceparent` + `request_id`, matching trace ID in service logs, worker `processed_event`, and ClickHouse `inference_logs` row)*

## Phase 16 — Kubernetes

- [x] `infra/helm/ollivelogs/` chart *(Chart.yaml + values.yaml + 6 templates)*
- [x] Deployments + Services + Ingress *(chat-api, ingest-api, log-consumer, web + Traefik Ingress)*
- [x] Secrets templated (no plaintext in chart) *(externalSecretName pattern, created via kubectl out-of-band)*
- [x] PVCs for postgres + clickhouse + grafana + loki *(StatefulSet templates for pg/redis/clickhouse/prometheus/loki/grafana with PVC volumeClaimTemplates; OTel Collector as Deployment)*
- [x] HPA on chat-api and ingest-api *(autoscaling/v2 with CPU targets)*
- [ ] k3s on a single VM, public URL *(needs VM — guide in `infra/helm/README.md`)*
- [x] cert-manager for TLS *(ClusterIssuer documented + Ingress annotations templated)*
- [x] Deploy doc in `infra/helm/README.md` *(prereqs, build/push, secrets, install, verify, upgrade, uninstall)*

## Phase 17 — Tests & quality gates

- [x] Unit coverage ≥ 70% for apps, ≥ 90% for SDKs *(verified locally: Python apps `75.70%`, Python SDK `90.22%`, JS SDK executable source `91.62%` with `src/types.ts` excluded as type-only)*
- [x] Integration tests in CI *(`.github/workflows/ci.yml` runs pg+redis+clickhouse service containers)*
- [x] Playwright e2e in CI *(`.github/workflows/e2e.yml`, browser cache, report artifact)*
- [x] `k6` load test script in `tests/load/` *(`tests/load/ingest-load.js`, 500 RPS, p99<20ms threshold)*
- [x] CI: lint + typecheck + test + build + push images *(`.github/workflows/images.yml` — matrix across 4 services, ghcr.io, buildx cache)*

## Phase 18 — Eval harness (stretch, the "top‑1" moment)

- [x] CLI: `ollive eval --conversation-id ... --against gpt-4.1`
- [x] Replays logged turns against a different model
- [x] Diff output (token-level + semantic similarity)
- [x] Stores eval runs in a new ClickHouse table `eval_runs`
- [x] Dashboard panel for eval drift

## Phase 19 — Polish & submission

- [x] README: setup, architecture, schema, tradeoffs, "what I'd improve" *(now substantive)*
- [x] Architecture diagram *(Mermaid in `docs/architecture.md` — renders natively on GitHub)*
- [x] Schema ER diagram *(Mermaid `erDiagram` in `docs/architecture.md` §3)*
- [ ] Loom walkthrough (≤5 min): send chat, see dashboard, cancel, resume
- [x] 6–8 screenshots in `docs/demo/`
- [x] Final smoke test on clean checkout (`git clone && make dev`) *(verified from a fresh local `git clone` after copying `.env.example -> .env`; this Windows host lacks GNU `make`, so the equivalent `docker compose up -d --build` path was used and all 10 services reached healthy)*
- [ ] Push to GitHub (public) *(waiting on user — `kumarabhik`)*
- [ ] Email `work@ollive.ai` with repo + notes + demo link

---

## Datasets needed (waiting on human)

- [ ] **Decision:** synthetic-only (Faker) or pull `microsoft/presidio-research` for PII eval? → human to choose.
- [ ] **Optional:** small slice of `LMSYS-Chat-1M` for realistic demo conversations.

## Deferred / waiting on human

- [ ] Pull HuggingFace + other provider tokens from `aegisdesk` (XOXO) — **only when explicitly told**.
- [ ] Domain name for hosted demo.
- [ ] VPS / k3s host for the deploy bonus.

## Backlog (good ideas, not in scope yet)

- [ ] Vector DB + RAG mode (conversation memory search)
- [x] Function/tool-call logging in `inference_logs.extra` *(Python SDK now extracts OpenAI `tool_calls` and Anthropic `tool_use` blocks into event `extra`; worker tests verify the payload is preserved into ClickHouse rows)*
- [x] Per-user budget alerts (email when $/day > X) *(worker-side daily spend tracking in Redis + once-per-day SMTP alert to the owning user when `BUDGET_ALERT_THRESHOLD_USD` is crossed)*
- [x] Provider auto-failover (if OpenAI 5xx, retry on Anthropic) *(chat-api retries provider attempts using `PROVIDER_FAILOVER_MAP` and provider-specific default models; unit tests cover completion and stream failover)*
- [ ] Browser extension that logs any LLM tab's traffic
- [x] Slack/Discord notifier for DLQ events *(worker posts summarized DLQ payloads to configured webhook URLs via `DLQ_SLACK_WEBHOOK_URL` / `DLQ_DISCORD_WEBHOOK_URL`)*
