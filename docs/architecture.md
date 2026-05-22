# Architecture · OlliveLogs

Visual companion to [DESIGN.md](../DESIGN.md). Mermaid diagrams render natively on GitHub.

---

## 1. System context

```mermaid
flowchart LR
    user([User])
    subgraph Browser
      web[Next.js Console<br/>SSE consumer]
    end
    subgraph App["Application plane"]
      chat[chat-api<br/>FastAPI · SSE]
      ingest[ingest-api<br/>FastAPI]
      worker[log-consumer<br/>Worker]
    end
    subgraph Data["Data plane"]
      pg[(Postgres<br/>OLTP)]
      ch[(ClickHouse<br/>OLAP)]
      redis[(Redis<br/>cache + streams)]
    end
    subgraph Obs["Observability"]
      otel[OTel Collector]
      prom[Prometheus]
      loki[Loki]
      graf[Grafana]
    end
    subgraph LLM["LLM Providers"]
      openai[OpenAI]
      anthropic[Anthropic]
      hf[HuggingFace router]
      others[Gemini / DeepSeek / Grok]
    end

    user --> web
    web -- "POST /v1/conversations/:id/messages (SSE)" --> chat
    chat --> openai
    chat --> anthropic
    chat --> hf
    chat --> others
    chat -- "ollivelogs-py SDK" --> ingest
    ingest --> redis
    worker -- "XREADGROUP cg-store" --> redis
    worker --> pg
    worker --> ch
    chat --> pg
    chat --> redis
    chat -- OTLP --> otel
    ingest -- OTLP --> otel
    worker -- OTLP --> otel
    otel -- "metrics :8889" --> prom
    otel -- "logs OTLP" --> loki
    prom --> graf
    loki --> graf
    ch --> graf
```

---

## 2. Send-a-message sequence (streaming + cancel)

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant C as chat-api
    participant R as Redis
    participant P as Provider (e.g. OpenAI)
    participant S as ollivelogs-py SDK
    participant I as ingest-api
    participant W as log-consumer
    participant PG as Postgres
    participant CH as ClickHouse

    U->>C: POST /v1/conversations/:id/messages {stream:true}
    C->>R: rate-limit check (sliding window)
    C->>PG: INSERT user message
    C->>R: DEL cancel:{conv_id}
    C->>P: stream completion
    loop per chunk
        P-->>C: token chunk
        C->>R: GET cancel:{conv_id}
        alt cancel set
            C-->>U: event: cancelled
            C->>PG: INSERT assistant partial (status=cancelled)
            C->>S: emit log (status=cancelled, partial preview)
        else
            C-->>U: event: token {delta}
        end
    end
    C->>PG: INSERT assistant (status=ok)
    C-->>U: event: done
    C->>S: emit log (status=ok, latency, tokens, cost)
    S->>I: POST /v1/logs (batched)
    I->>R: SET NX event_id (dedup)
    I->>R: XADD logs.raw
    I-->>S: 200 {accepted, deduped}
    W->>R: XREADGROUP cg-store
    W->>W: Presidio redact previews
    W->>PG: UPSERT message (by event_id)
    W->>CH: INSERT inference_logs
    W->>R: XACK
```

The cancel flag is **idempotent and TTL-bounded** so a forgotten flag from a previous attempt can't kill a fresh request.

---

## 3. Data model

```mermaid
erDiagram
    USERS ||--o{ CONVERSATIONS : owns
    CONVERSATIONS ||--o{ MESSAGES : contains
    MESSAGES ||--o| MESSAGES_FULL : "encrypted raw (optional)"
    USERS {
        uuid id PK
        citext email UK
        text display_name
        timestamptz created_at
    }
    CONVERSATIONS {
        uuid id PK
        uuid user_id FK
        text title
        text status "active | cancelled | archived"
        text model_default
        timestamptz created_at
        timestamptz updated_at
    }
    MESSAGES {
        uuid id PK
        uuid conversation_id FK
        text role "system|user|assistant|tool"
        text content "redacted preview"
        uuid content_full_id FK "nullable"
        int token_count
        text status "ok|error|cancelled|partial"
        uuid event_id UK "from SDK, idempotency anchor"
        timestamptz created_at
    }
    MESSAGES_FULL {
        uuid id PK
        bytea content_enc "pgcrypto, gated by STORE_RAW"
        timestamptz created_at
    }
```

**ClickHouse `inference_logs`** is a separate, flat append-only table — no foreign keys, no joins. Joined logically by `event_id` for forensic queries.

---

## 4. Failure modes

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Healthy

    Healthy --> ProviderError: 5xx / timeout
    ProviderError --> Healthy: SDK retry (2x jittered)
    ProviderError --> RecordFailure: still failing
    RecordFailure --> Healthy: log emitted with status=error

    Healthy --> IngestDown: ingest-api unreachable
    IngestDown --> SDKQueue: SDK buffers in memory (max 1k)
    SDKQueue --> Healthy: ingest recovered, drain
    SDKQueue --> DroppedEvents: queue full
    DroppedEvents --> Healthy: counter incremented, app keeps serving

    Healthy --> WorkerDown: log-consumer crashed
    WorkerDown --> StreamBacklog: events accumulate on logs.raw
    StreamBacklog --> Healthy: new worker resumes via cg-store

    Healthy --> CHDown: ClickHouse down
    CHDown --> PGOnly: worker keeps PG writes
    PGOnly --> Healthy: CH back, replay from side queue

    Healthy --> BadPayload: schema mismatch
    BadPayload --> Healthy: ingest 422, no retry
```

---

## 5. Deployment topology — k3s (single VM)

```mermaid
flowchart TB
    subgraph VM["k3s VM (2 vCPU / 4 GB)"]
        direction TB
        ing[Traefik Ingress<br/>TLS via cert-manager]
        subgraph ns["Namespace: ollivelogs"]
            web[Deployment: web<br/>2 replicas]
            chat[Deployment: chat-api<br/>HPA 2-6]
            ingestd[Deployment: ingest-api<br/>HPA 2-8]
            workerd[Deployment: log-consumer<br/>2 replicas]
            pgsts[StatefulSet: postgres<br/>PVC 20Gi]
            chsts[StatefulSet: clickhouse<br/>PVC 50Gi]
            rsts[StatefulSet: redis<br/>PVC 5Gi]
            promsts[StatefulSet: prometheus<br/>PVC 10Gi]
            lokists[StatefulSet: loki<br/>PVC 10Gi]
            grafsts[StatefulSet: grafana<br/>PVC 5Gi]
            otelcol[Deployment: otel-collector]
        end
    end

    ing --> web
    ing --> chat
    ing --> grafsts
    chat --> pgsts
    chat --> rsts
    chat --> otelcol
    ingestd --> rsts
    ingestd --> otelcol
    workerd --> rsts
    workerd --> pgsts
    workerd --> chsts
    workerd --> otelcol
    otelcol --> promsts
    otelcol --> lokists
    promsts --> grafsts
    lokists --> grafsts
    chsts --> grafsts
```

HPA targets:

- **chat-api**: CPU 70% or 50 RPS/pod
- **ingest-api**: CPU 60% or 300 RPS/pod
- **log-consumer**: not autoscaled (stream backpressure is the regulator)

PVCs are sized for a 30-day demo run. ClickHouse partitions are dropped at `TTL ts + INTERVAL 90 DAY`.

---

## 6. Event schema (SDK → ingest)

```mermaid
classDiagram
    class InferenceEvent {
        +uuid event_id
        +datetime ts
        +uuid conversation_id
        +uuid? user_id
        +string client          "py-sdk | js-sdk | web"
        +string sdk_version
        +string provider
        +string model
        +Request request
        +Response response
        +Usage usage
        +Timing timing
        +string status          "ok | error | cancelled | timeout"
        +Error? error
        +float cost_usd
        +int event_schema_version
    }
    class Request {
        +string messages_preview  "≤2KB, redacted"
        +bool stream
        +float temperature
    }
    class Response {
        +string text_preview      "≤2KB, redacted"
        +string finish_reason
    }
    class Usage {
        +int prompt_tokens
        +int completion_tokens
        +int total_tokens
    }
    class Timing {
        +int latency_ms
        +int ttft_ms
    }
    class Error {
        +string kind             "timeout | rate_limit | 5xx | client_abort"
        +string message
    }
    InferenceEvent --> Request
    InferenceEvent --> Response
    InferenceEvent --> Usage
    InferenceEvent --> Timing
    InferenceEvent --> Error
```

Validation lives in [apps/ingest-api/ingest_app/schemas.py](../apps/ingest-api/ingest_app/schemas.py). Schema is versioned via `event_schema_version`.

---

## 7. Observability pipeline

```mermaid
flowchart LR
    subgraph emit[Emitters]
      chat[chat-api]
      ingest[ingest-api]
      worker[log-consumer]
    end
    subgraph collect[Collector + Scrapers]
      otel[OTel Collector<br/>OTLP gRPC/HTTP]
      prom[Prometheus<br/>scrapes /metrics]
    end
    subgraph stores[Stores]
      tsdb[(Prometheus TSDB)]
      loki[(Loki TSDB)]
      ch[(ClickHouse)]
    end
    subgraph ui[UI]
      graf[Grafana<br/>3 dashboards]
    end

    chat -- OTLP traces+logs --> otel
    ingest -- OTLP --> otel
    worker -- OTLP --> otel
    chat -- /metrics --> prom
    ingest -- /metrics --> prom
    worker -- /metrics --> prom
    otel -- exposition :8889 --> prom
    otel -- OTLP logs --> loki
    prom --> tsdb
    tsdb --> graf
    loki --> graf
    ch --> graf
```

Dashboards (committed JSON under [infra/grafana/dashboards/](../infra/grafana/dashboards/)):

1. **Inference Health** — RPS, p50/95/99 latency, TTFT, error rate, slowest 25 calls
2. **Cost & Tokens** — $/min by provider, top conversations by spend, tokens in vs out
3. **Ingestion Pipeline** — events/sec by status, dedup rate, ingest p99, worker lag, DLQ count
