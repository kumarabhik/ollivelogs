-- OlliveLogs — ClickHouse schema (OLAP for inference logs)
-- Auto-loaded by clickhouse-server on container start.

CREATE DATABASE IF NOT EXISTS ollivelogs;

CREATE TABLE IF NOT EXISTS ollivelogs.inference_logs
(
    event_id           UUID,
    ts                 DateTime64(3, 'UTC'),
    conversation_id    UUID,
    user_id            Nullable(UUID),

    provider           LowCardinality(String),
    model              LowCardinality(String),
    status             LowCardinality(String),   -- ok | error | cancelled | timeout
    error_kind         LowCardinality(String),   -- '' if ok

    latency_ms         UInt32,
    ttft_ms            UInt32 DEFAULT 0,         -- time-to-first-token for streaming
    prompt_tokens      UInt32 DEFAULT 0,
    completion_tokens  UInt32 DEFAULT 0,
    total_tokens       UInt32 DEFAULT 0,
    cost_usd           Float64 DEFAULT 0,

    input_preview      String,                   -- redacted, ≤256 chars
    output_preview     String,                   -- redacted, ≤256 chars

    client             LowCardinality(String),   -- web | py-sdk | js-sdk
    sdk_version        LowCardinality(String),
    request_id         String,                   -- correlate with traces

    extra              String                    -- JSON escape hatch
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (ts, provider, model)
TTL toDateTime(ts) + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

-- Convenience materialized view: per-minute rollup for dashboards.
CREATE TABLE IF NOT EXISTS ollivelogs.inference_minute_agg
(
    minute             DateTime,
    provider           LowCardinality(String),
    model              LowCardinality(String),
    status             LowCardinality(String),
    requests           UInt64,
    avg_latency_ms     Float64,
    p95_latency_ms     Float64,
    tokens_in          UInt64,
    tokens_out         UInt64,
    cost_usd           Float64
)
ENGINE = SummingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (minute, provider, model, status);

CREATE MATERIALIZED VIEW IF NOT EXISTS ollivelogs.mv_inference_minute
TO ollivelogs.inference_minute_agg
AS
SELECT
    toStartOfMinute(ts)                                AS minute,
    provider,
    model,
    status,
    count()                                            AS requests,
    avg(latency_ms)                                    AS avg_latency_ms,
    quantileTDigest(0.95)(latency_ms)                  AS p95_latency_ms,
    sum(prompt_tokens)                                 AS tokens_in,
    sum(completion_tokens)                             AS tokens_out,
    sum(cost_usd)                                      AS cost_usd
FROM ollivelogs.inference_logs
GROUP BY minute, provider, model, status;

CREATE TABLE IF NOT EXISTS ollivelogs.eval_runs
(
    run_id                UUID,
    ts                    DateTime64(3, 'UTC'),
    conversation_id       UUID,
    turn_index            UInt16,
    source_provider       LowCardinality(String),
    source_model          LowCardinality(String),
    against_provider      LowCardinality(String),
    against_model         LowCardinality(String),
    status                LowCardinality(String),
    latency_ms            UInt32,
    prompt_tokens         UInt32 DEFAULT 0,
    completion_tokens     UInt32 DEFAULT 0,
    total_tokens          UInt32 DEFAULT 0,
    cost_usd              Float64 DEFAULT 0,
    token_similarity      Float64 DEFAULT 0,
    semantic_similarity   Float64 DEFAULT 0,
    prompt_preview        String,
    baseline_preview      String,
    candidate_preview     String,
    request_id            String,
    extra                 String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (ts, conversation_id, run_id, turn_index)
TTL toDateTime(ts) + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;
