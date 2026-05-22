"use client";

import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { withTelemetryHeaders } from "@/lib/telemetry";

type Summary = {
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
  by_provider: Array<{ provider: string; requests: number; cost_usd: number }>;
  eval_drift: Array<{
    against_provider: string;
    against_model: string;
    avg_semantic_similarity: number;
    avg_token_similarity: number;
    runs: number;
  }>;
  source: "clickhouse" | "mock";
};

function fmt(n: number, opts: Intl.NumberFormatOptions = {}) {
  return new Intl.NumberFormat("en-US", opts).format(n);
}

function shortMinute(iso: string) {
  return iso.slice(11, 16);
}

function shortTarget(provider: string, model: string) {
  return `${provider}/${model}`.replace("conversation-history", "history");
}

export default function DashboardClient({ initialSummary }: { initialSummary: Summary }) {
  const [summary, setSummary] = useState<Summary>(initialSummary);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    const t = setInterval(async () => {
      setRefreshing(true);
      try {
        const res = await fetch("/api/v1/analytics/summary", {
          cache: "no-store",
          headers: withTelemetryHeaders(),
        });
        if (res.ok) setSummary(await res.json());
      } finally {
        setRefreshing(false);
      }
    }, 15_000);
    return () => clearInterval(t);
  }, []);

  return (
    <main className="min-h-screen bg-background px-6 py-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex items-baseline justify-between">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">Inference dashboard</h1>
            <p className="text-sm text-muted-foreground">
              Live counters from ClickHouse (`inference_logs`). Refresh every 15s.
            </p>
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className={refreshing ? "animate-pulse" : ""}>
              {refreshing ? "refreshing…" : "up to date"}
            </span>
            <span className="rounded-full border px-2 py-0.5">
              source: {summary.source}
            </span>
          </div>
        </header>

        <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Requests (24h)" value={fmt(summary.totals.requests_24h)} />
          <Stat
            label="Spend (24h)"
            value={`$${fmt(summary.totals.cost_usd_24h, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 4,
            })}`}
          />
          <Stat label="Tokens (24h)" value={fmt(summary.totals.tokens_24h)} />
          <Stat
            label="Error rate"
            value={`${summary.totals.error_rate_pct.toFixed(2)}%`}
            danger={summary.totals.error_rate_pct > 2}
          />
        </section>

        <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Requests / minute (last hour)</CardTitle>
            </CardHeader>
            <CardContent className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={summary.timeseries.map((p) => ({ ...p, t: shortMinute(p.minute) }))}>
                  <defs>
                    <linearGradient id="reqGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.45} />
                      <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3} />
                  <XAxis dataKey="t" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Area
                    type="monotone"
                    dataKey="requests"
                    stroke="hsl(var(--primary))"
                    fill="url(#reqGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>p95 latency (ms)</CardTitle>
            </CardHeader>
            <CardContent className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={summary.timeseries.map((p) => ({ ...p, t: shortMinute(p.minute) }))}>
                  <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3} />
                  <XAxis dataKey="t" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} unit="ms" />
                  <Tooltip />
                  <Line
                    type="monotone"
                    dataKey="p95_latency_ms"
                    stroke="hsl(var(--primary))"
                    dot={false}
                    strokeWidth={2}
                  />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </section>

        <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Spend by provider (24h)</CardTitle>
            </CardHeader>
            <CardContent className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={summary.by_provider}>
                  <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3} />
                  <XAxis dataKey="provider" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 11 }} unit="$" />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="cost_usd" name="$ spend" fill="hsl(var(--primary))" />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Requests by provider (24h)</CardTitle>
            </CardHeader>
            <CardContent className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={summary.by_provider}>
                  <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3} />
                  <XAxis dataKey="provider" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="requests" name="requests" fill="hsl(var(--primary))" />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </section>

        <section className="grid grid-cols-1 gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Eval drift by target model</CardTitle>
            </CardHeader>
            <CardContent className="h-72">
              {summary.eval_drift.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  Run <code>ollive eval --conversation-id ... --against gpt-4.1</code> to
                  populate this panel.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={summary.eval_drift.map((item) => ({
                      ...item,
                      target: shortTarget(item.against_provider, item.against_model),
                    }))}
                  >
                    <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3} />
                    <XAxis dataKey="target" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} domain={[0, 1]} />
                    <Tooltip formatter={(value: number) => `${(value * 100).toFixed(1)}%`} />
                    <Legend />
                    <Bar
                      dataKey="avg_semantic_similarity"
                      name="semantic similarity"
                      fill="hsl(var(--primary))"
                    />
                    <Bar
                      dataKey="avg_token_similarity"
                      name="token similarity"
                      fill="hsl(var(--secondary-foreground))"
                    />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>
        </section>

        <footer className="pt-4 text-xs text-muted-foreground">
          Powered by ClickHouse `inference_logs` + minute rollup MV. For richer
          breakdowns see the Grafana dashboards under <code>/grafana</code>.
        </footer>
      </div>
    </main>
  );
}

function Stat({ label, value, danger }: { label: string; value: string; danger?: boolean }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-semibold ${danger ? "text-red-500" : ""}`}>{value}</div>
      </CardContent>
    </Card>
  );
}
