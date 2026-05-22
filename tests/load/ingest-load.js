// OlliveLogs — k6 load test for the ingest pipeline.
//
// Run:
//   k6 run tests/load/ingest-load.js
// Override URL / RPS:
//   k6 run -e INGEST_URL=http://localhost:8002/v1/logs -e VUS=100 tests/load/ingest-load.js
//
// Asserts the DESIGN.md §10 SLO: p99 < 20ms at ~500 RPS sustained.

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Counter } from "k6/metrics";
import { randomString, uuidv4 } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const INGEST_URL =
  __ENV.INGEST_URL || "http://localhost:8002/v1/logs";
const VUS = parseInt(__ENV.VUS || "50", 10);
const DURATION = __ENV.DURATION || "1m";

export const options = {
  scenarios: {
    sustained_throughput: {
      executor: "constant-arrival-rate",
      rate: parseInt(__ENV.TARGET_RPS || "500", 10),
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: VUS,
      maxVUs: VUS * 4,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.005"],
    http_req_duration: ["p(99)<20", "p(95)<10"],
    // custom:
    ingest_accepted: ["count>0"],
  },
};

const accepted = new Counter("ingest_accepted");
const dedup = new Counter("ingest_deduped");
const latency = new Trend("ingest_latency_ms");

const PROVIDERS = ["openai", "anthropic", "huggingface"];
const MODELS = {
  openai: ["gpt-4.1-mini", "gpt-4o"],
  anthropic: ["claude-3-5-sonnet", "claude-3-5-haiku"],
  huggingface: ["Qwen/Qwen2.5-72B-Instruct", "meta-llama/Llama-3.1-70B-Instruct"],
};

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function buildEvent() {
  const provider = pick(PROVIDERS);
  const model = pick(MODELS[provider]);
  const latencyMs = 200 + Math.floor(Math.random() * 1800);
  const promptTokens = 20 + Math.floor(Math.random() * 200);
  const completionTokens = 20 + Math.floor(Math.random() * 400);
  return {
    event_id: uuidv4(),
    ts: new Date().toISOString(),
    conversation_id: uuidv4(),
    user_id: uuidv4(),
    client: "k6-load",
    sdk_version: "0.1.0",
    provider,
    model,
    request: {
      messages_preview: `user: ${randomString(64)}`,
      stream: false,
      temperature: 0.7,
    },
    response: {
      text_preview: `assistant: ${randomString(96)}`,
      finish_reason: "stop",
    },
    usage: {
      prompt_tokens: promptTokens,
      completion_tokens: completionTokens,
      total_tokens: promptTokens + completionTokens,
    },
    timing: { latency_ms: latencyMs, ttft_ms: Math.floor(latencyMs / 4) },
    status: Math.random() < 0.98 ? "ok" : "error",
    error: null,
    cost_usd: (promptTokens * 0.00001 + completionTokens * 0.00003),
  };
}

export default function () {
  const body = JSON.stringify([buildEvent()]);
  const res = http.post(INGEST_URL, body, {
    headers: { "Content-Type": "application/json" },
    tags: { endpoint: "ingest" },
  });

  latency.add(res.timings.duration);

  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "valid JSON body": (r) => {
      try {
        return typeof JSON.parse(r.body) === "object";
      } catch (_e) {
        return false;
      }
    },
  });

  if (ok && res.status === 200) {
    const data = JSON.parse(res.body);
    accepted.add(data.accepted || 0);
    dedup.add(data.deduped || 0);
  }

  // Light pacing so a flaky VU doesn't hot-loop.
  if (Math.random() < 0.05) sleep(0.01);
}

export function handleSummary(data) {
  return {
    stdout: textSummary(data),
  };
}

function textSummary(data) {
  const m = data.metrics;
  const dur = m.http_req_duration?.values || {};
  const fail = m.http_req_failed?.values?.rate || 0;
  return [
    "",
    "─── OlliveLogs ingest load summary ───",
    `target_url     : ${INGEST_URL}`,
    `target_rps     : ${__ENV.TARGET_RPS || 500}/s`,
    `duration       : ${DURATION}`,
    `p50 / p95 / p99: ${dur["p(50)"]?.toFixed(2)}ms / ${dur["p(95)"]?.toFixed(2)}ms / ${dur["p(99)"]?.toFixed(2)}ms`,
    `avg / max      : ${dur.avg?.toFixed(2)}ms / ${dur.max?.toFixed(2)}ms`,
    `error rate     : ${(fail * 100).toFixed(3)}%`,
    `accepted       : ${m.ingest_accepted?.values?.count || 0}`,
    `deduped        : ${m.ingest_deduped?.values?.count || 0}`,
    "",
  ].join("\n");
}
