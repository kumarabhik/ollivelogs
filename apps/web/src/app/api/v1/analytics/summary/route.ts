/**
 * GET /api/v1/analytics/summary
 *
 * Server-side route that proxies dashboard queries to ClickHouse over HTTP.
 * Reads CLICKHOUSE_URL (default http://clickhouse:8123) from env.
 *
 * Returns JSON suitable for the /dashboard page's Recharts panels.
 * Falls back to deterministic mock data when ClickHouse is unreachable
 * so the page renders during local frontend-only development.
 */

import { NextResponse } from "next/server";

const CLICKHOUSE_URL = process.env.CLICKHOUSE_URL ?? "http://clickhouse:8123";
const CLICKHOUSE_DB = process.env.CLICKHOUSE_DB ?? "ollivelogs";

type SummaryPayload = {
  totals: {
    requests_24h: number;
    cost_usd_24h: number;
    tokens_24h: number;
    error_rate_pct: number;
  };
  timeseries: Array<{
    minute: string;
    requests: number;
    p95_latency_ms: number;
    cost_usd: number;
  }>;
  by_provider: Array<{
    provider: string;
    requests: number;
    cost_usd: number;
  }>;
  eval_drift: Array<{
    against_provider: string;
    against_model: string;
    avg_semantic_similarity: number;
    avg_token_similarity: number;
    runs: number;
  }>;
  source: "clickhouse" | "mock";
};

type TotalsRow = {
  requests_24h: number | string;
  cost_usd_24h: number | string | null;
  tokens_24h: number | string | null;
  error_rate_pct: number | string | null;
};

type TimeseriesRow = {
  minute: string;
  requests: number | string;
  p95_latency_ms: number | string | null;
  cost_usd: number | string | null;
};

type ProviderRow = {
  provider: string;
  requests: number | string;
  cost_usd: number | string | null;
};

type EvalDriftRow = {
  against_provider: string;
  against_model: string;
  avg_semantic_similarity: number | string | null;
  avg_token_similarity: number | string | null;
  runs: number | string;
};

async function ch<T>(sql: string, signal: AbortSignal): Promise<T[]> {
  const url = `${CLICKHOUSE_URL}/?database=${CLICKHOUSE_DB}&default_format=JSON`;
  const res = await fetch(url, {
    method: "POST",
    body: sql,
    headers: { "Content-Type": "text/plain" },
    signal,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`ClickHouse ${res.status}: ${await res.text()}`);
  const body = (await res.json()) as { data?: T[] };
  return body.data ?? [];
}

function asNumber(value: number | string | null | undefined): number {
  if (typeof value === "number") {
    return value;
  }
  if (typeof value === "string" && value !== "") {
    return Number(value);
  }
  return 0;
}

function mockSummary(): SummaryPayload {
  const now = Date.now();
  const minutes = Array.from({ length: 60 }, (_, i) => {
    const minute = new Date(now - (59 - i) * 60_000).toISOString();
    return {
      minute,
      requests: 80 + Math.round(40 * Math.sin(i / 6) + Math.random() * 30),
      p95_latency_ms: 700 + Math.round(300 * Math.cos(i / 8) + Math.random() * 100),
      cost_usd: Number((0.02 + Math.random() * 0.03).toFixed(4)),
    };
  });
  return {
    totals: {
      requests_24h: 18_432,
      cost_usd_24h: 12.4831,
      tokens_24h: 2_140_882,
      error_rate_pct: 0.42,
    },
    timeseries: minutes,
    by_provider: [
      { provider: "openai", requests: 9421, cost_usd: 6.81 },
      { provider: "anthropic", requests: 5117, cost_usd: 4.02 },
      { provider: "huggingface", requests: 3894, cost_usd: 1.65 },
    ],
    eval_drift: [
      {
        against_provider: "openai",
        against_model: "gpt-4.1",
        avg_semantic_similarity: 0.84,
        avg_token_similarity: 0.72,
        runs: 12,
      },
      {
        against_provider: "anthropic",
        against_model: "claude-3-7-sonnet",
        avg_semantic_similarity: 0.79,
        avg_token_similarity: 0.68,
        runs: 8,
      },
    ],
    source: "mock",
  };
}

export async function GET() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2500);
  try {
    const totalsSql = `
      SELECT
        count() AS requests_24h,
        sum(cost_usd) AS cost_usd_24h,
        sum(total_tokens) AS tokens_24h,
        100 * sum(if(status IN ('error','timeout'), 1, 0)) / greatest(count(), 1) AS error_rate_pct
      FROM inference_logs WHERE ts > now() - INTERVAL 24 HOUR FORMAT JSON
    `;
    const timeseriesSql = `
      SELECT
        formatDateTime(toStartOfMinute(ts), '%Y-%m-%dT%H:%i:00Z') AS minute,
        count() AS requests,
        quantileTDigest(0.95)(latency_ms) AS p95_latency_ms,
        sum(cost_usd) AS cost_usd
      FROM inference_logs
      WHERE ts > now() - INTERVAL 1 HOUR
      GROUP BY minute ORDER BY minute FORMAT JSON
    `;
    const byProviderSql = `
      SELECT provider, count() AS requests, sum(cost_usd) AS cost_usd
      FROM inference_logs WHERE ts > now() - INTERVAL 24 HOUR
      GROUP BY provider ORDER BY requests DESC FORMAT JSON
    `;
    const evalDriftSql = `
      SELECT
        against_provider,
        against_model,
        avg(semantic_similarity) AS avg_semantic_similarity,
        avg(token_similarity) AS avg_token_similarity,
        countDistinct(run_id) AS runs
      FROM eval_runs
      WHERE ts > now() - INTERVAL 30 DAY
        AND status = 'ok'
      GROUP BY against_provider, against_model
      ORDER BY avg_semantic_similarity ASC, runs DESC
      LIMIT 8 FORMAT JSON
    `;

    const [totalsRaw, timeseriesRaw, byProviderRaw, evalDriftRaw] = await Promise.all([
      ch<TotalsRow>(totalsSql, controller.signal),
      ch<TimeseriesRow>(timeseriesSql, controller.signal),
      ch<ProviderRow>(byProviderSql, controller.signal),
      ch<EvalDriftRow>(evalDriftSql, controller.signal),
    ]);

    clearTimeout(timer);
    const totals = totalsRaw[0];
    const timeseries = timeseriesRaw.map((row) => ({
      minute: row.minute,
      requests: asNumber(row.requests),
      p95_latency_ms: asNumber(row.p95_latency_ms),
      cost_usd: asNumber(row.cost_usd),
    }));
    const byProvider = byProviderRaw.map((row) => ({
      provider: row.provider,
      requests: asNumber(row.requests),
      cost_usd: asNumber(row.cost_usd),
    }));
    const evalDrift = evalDriftRaw.map((row) => ({
      against_provider: row.against_provider,
      against_model: row.against_model,
      avg_semantic_similarity: asNumber(row.avg_semantic_similarity),
      avg_token_similarity: asNumber(row.avg_token_similarity),
      runs: asNumber(row.runs),
    }));
    return NextResponse.json({
      totals:
        totals === undefined
          ? { requests_24h: 0, cost_usd_24h: 0, tokens_24h: 0, error_rate_pct: 0 }
          : {
              requests_24h: asNumber(totals.requests_24h),
              cost_usd_24h: asNumber(totals.cost_usd_24h),
              tokens_24h: asNumber(totals.tokens_24h),
              error_rate_pct: asNumber(totals.error_rate_pct),
            },
      timeseries,
      by_provider: byProvider,
      eval_drift: evalDrift,
      source: "clickhouse" as const,
    } satisfies SummaryPayload);
  } catch (err) {
    clearTimeout(timer);
    console.warn("[analytics/summary] ClickHouse unreachable, returning mock:", err);
    return NextResponse.json(mockSummary());
  }
}
