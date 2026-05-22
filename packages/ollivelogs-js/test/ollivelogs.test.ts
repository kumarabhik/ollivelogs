import { afterEach, describe, expect, it, vi } from "vitest";

import { OlliveLogs } from "../src";
import { BatchedEventShipper } from "../src/shipper";
import { FetchLike, InferenceEvent } from "../src/types";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ollivelogs-js", () => {
  it("wraps an OpenAI-style client and emits a batch", async () => {
    const capturedBatches: InferenceEvent[][] = [];
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 1,
      flushIntervalMs: 5,
      fetch: captureTransport(capturedBatches),
    });

    const openaiClient = {
      chat: {
        completions: {
          create: async (request: { model: string; messages: Array<{ role: string; content: string }> }) => {
            expect(request.model).toBe("gpt-4.1-mini");
            expect(request.messages[0]?.content).toBe("Hello");
            return {
              choices: [
                {
                  message: { content: "Hi there" },
                  finish_reason: "stop",
                },
              ],
              usage: {
                prompt_tokens: 3,
                completion_tokens: 2,
                total_tokens: 5,
              },
            };
          },
        },
      },
    };

    const wrapped = sdk.wrap(openaiClient, {
      conversationId: "conv-openai",
      userId: "user-1",
    });

    await wrapped.chat.completions.create({
      model: "gpt-4.1-mini",
      messages: [{ role: "user", content: "Hello" }],
    });
    await sdk.flush();
    await sdk.close();

    expect(capturedBatches).toHaveLength(1);
    expect(capturedBatches[0]?.[0]?.provider).toBe("openai");
    expect(capturedBatches[0]?.[0]?.response.text_preview).toBe("Hi there");
    expect(capturedBatches[0]?.[0]?.usage.total_tokens).toBe(5);
  });

  it("wraps fetch for Node or browser style calls", async () => {
    const capturedBatches: InferenceEvent[][] = [];
    const fetchImpl: FetchLike = vi.fn(async () => {
      return new Response(JSON.stringify({ ok: true, answer: "done" }), {
        status: 200,
        headers: {
          "content-type": "application/json",
        },
      });
    });
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 1,
      flushIntervalMs: 5,
      fetch: captureTransport(capturedBatches),
    });

    const wrappedFetch = sdk.wrapFetch(fetchImpl, {
      conversationId: "conv-fetch",
      provider: "browser",
      model: "fetch-client",
    });

    const response = await wrappedFetch("https://api.test/chat", {
      method: "POST",
      body: JSON.stringify({ prompt: "hello" }),
    });

    expect(response.ok).toBe(true);
    await sdk.flush();
    await sdk.close();

    expect(capturedBatches).toHaveLength(1);
    expect(capturedBatches[0]?.[0]?.provider).toBe("browser");
    expect(capturedBatches[0]?.[0]?.request.messages_preview).toContain('"prompt":"hello"');
    expect(capturedBatches[0]?.[0]?.response.text_preview).toContain('"answer":"done"');
  });

  it("uses sendBeacon on unload when events are still queued", async () => {
    const lifecycle = new FakeUnloadTarget();
    const beaconPayloads: string[] = [];
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 50,
      flushIntervalMs: 60_000,
      fetch: vi.fn(async () => new Response(null, { status: 200 })),
      sendBeacon: (_url: string, data: BodyInit) => {
        beaconPayloads.push(typeof data === "string" ? data : "");
        return true;
      },
      unloadTarget: lifecycle,
    });

    const wrappedFetch = sdk.wrapFetch(
      async () => new Response("queued payload", { status: 200 }),
      { conversationId: "conv-unload" },
    );
    await wrappedFetch("https://api.test/chat");

    lifecycle.dispatch("pagehide");
    await sdk.close();

    expect(beaconPayloads).toHaveLength(1);
    expect(beaconPayloads[0]).toContain("conv-unload");
  });

  it("records wrapped OpenAI streaming responses", async () => {
    const capturedBatches: InferenceEvent[][] = [];
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 1,
      flushIntervalMs: 5,
      fetch: captureTransport(capturedBatches),
    });

    const openaiClient = {
      chat: {
        completions: {
          create: (_request: Record<string, unknown>) =>
            streamChunks([
              {
                choices: [{ delta: { content: "Hello " }, finish_reason: null }],
              },
              {
                choices: [{ delta: { content: "world" }, finish_reason: "stop" }],
                usage: { prompt_tokens: 2, completion_tokens: 2, total_tokens: 4 },
              },
            ]),
        },
      },
    };

    const wrapped = sdk.wrap(openaiClient, {
      conversationId: "conv-stream",
      provider: "openai",
    });

    const streamed: unknown[] = [];
    for await (const chunk of wrapped.chat.completions.create({
      model: "gpt-4.1-mini",
      messages: [{ role: "user", content: "Stream please" }],
      stream: true,
    }) as AsyncIterable<unknown>) {
      streamed.push(chunk);
    }
    await sdk.flush();
    await sdk.close();

    expect(streamed).toHaveLength(2);
    expect(capturedBatches[0]?.[0]?.response.text_preview).toBe("Hello world");
    expect(capturedBatches[0]?.[0]?.usage.total_tokens).toBe(4);
  });

  it("records synchronous OpenAI responses and truncates long previews", async () => {
    const capturedBatches: InferenceEvent[][] = [];
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 1,
      flushIntervalMs: 5,
      fetch: captureTransport(capturedBatches),
    });
    const longText = "x".repeat(600);

    const openaiClient = {
      chat: {
        completions: {
          create: (_request: Record<string, unknown>) => ({
            choices: [
              {
                message: { content: [{ text: longText }] },
                finish_reason: "stop",
              },
            ],
            usage: {
              prompt_tokens: 10,
              completion_tokens: 20,
              total_tokens: 30,
            },
          }),
        },
      },
    };

    sdk.wrap(openaiClient, { conversationId: "conv-sync" }).chat.completions.create({
      model: "gpt-4.1-mini",
      messages: [{ role: "user", content: [{ text: longText }] }],
    });
    await sdk.flush();
    await sdk.close();

    expect(capturedBatches[0]?.[0]?.response.text_preview?.endsWith("...")).toBe(true);
    expect(capturedBatches[0]?.[0]?.request.messages_preview?.endsWith("...")).toBe(true);
  });

  it("records wrapped OpenAI errors", async () => {
    const capturedBatches: InferenceEvent[][] = [];
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 1,
      flushIntervalMs: 5,
      fetch: captureTransport(capturedBatches),
    });

    const openaiClient = {
      chat: {
        completions: {
          create: async (_request: Record<string, unknown>) => {
            throw new Error("boom");
          },
        },
      },
    };

    const wrapped = sdk.wrap(openaiClient, { conversationId: "conv-error" });

    await expect(
      wrapped.chat.completions.create({
        model: "gpt-4.1-mini",
        messages: [{ role: "user", content: "Hello" }],
      }),
    ).rejects.toThrow("boom");
    await sdk.flush();
    await sdk.close();

    expect(capturedBatches[0]?.[0]?.status).toBe("error");
    expect(capturedBatches[0]?.[0]?.error?.kind).toBe("Error");
  });

  it("records fetch failures and thrown errors", async () => {
    const capturedBatches: InferenceEvent[][] = [];
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 1,
      flushIntervalMs: 5,
      fetch: captureTransport(capturedBatches),
    });

    const failedFetch = sdk.wrapFetch(
      async () => new Response("service down", { status: 503 }),
      { conversationId: "conv-fetch-error", provider: "browser" },
    );
    const throwingFetch = sdk.wrapFetch(
      async () => {
        throw new Error("network down");
      },
      { conversationId: "conv-fetch-throw", provider: "browser" },
    );

    const failedResponse = await failedFetch("https://api.test/chat", {
      method: "POST",
      body: new URLSearchParams({ prompt: "hello" }),
    });
    expect(failedResponse.status).toBe(503);

    await expect(
      throwingFetch(
        new Request("https://api.test/chat", {
          method: "POST",
          body: JSON.stringify({ prompt: "hello" }),
        }),
      ),
    ).rejects.toThrow("network down");

    await sdk.flush();
    await sdk.close();

    expect(capturedBatches).toHaveLength(2);
    expect(capturedBatches[0]?.[0]?.status).toBe("error");
    expect(capturedBatches[0]?.[0]?.request.messages_preview).toContain("prompt=hello");
    expect(capturedBatches[1]?.[0]?.error?.message).toBe("network down");
  });

  it("normalizes non-Error fetch throws and uses method-url fallback previews", async () => {
    const capturedBatches: InferenceEvent[][] = [];
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 1,
      flushIntervalMs: 5,
      fetch: captureTransport(capturedBatches),
    });
    const wrappedFetch = sdk.wrapFetch(
      async () => {
        throw "bad-news";
      },
      { conversationId: "conv-fetch-string", provider: "browser" },
    );

    await expect(wrappedFetch("https://api.test/fallback")).rejects.toBe("bad-news");
    await sdk.flush();
    await sdk.close();

    expect(capturedBatches[0]?.[0]?.request.messages_preview).toBe("GET https://api.test/fallback");
    expect(capturedBatches[0]?.[0]?.error?.message).toBe("bad-news");
  });

  it("requires a conversationId in default or per-call context", async () => {
    const sdk = new OlliveLogs({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 1,
      flushIntervalMs: 5,
      fetch: captureTransport([]),
    });

    const wrappedFetch = sdk.wrapFetch(async () => new Response("ok", { status: 200 }));

    await expect(wrappedFetch("https://api.test/chat")).rejects.toThrow("conversationId");
    await sdk.close();
  });

  it("shipper retries, drops, and requeues beacon batches correctly", async () => {
    const sentBodies: string[] = [];
    let attempts = 0;
    const shipper = new BatchedEventShipper({
      endpoint: "https://ingest.test/v1/logs",
      batchSize: 3,
      flushIntervalMs: 5,
      queueMax: 2,
      maxRetries: 2,
      fetchImpl: async (_input, init) => {
        attempts += 1;
        if (attempts < 3) {
          return new Response(null, { status: 503 });
        }
        sentBodies.push(String(init?.body ?? ""));
        return new Response(null, { status: 200 });
      },
      sendBeacon: (_url, _data) => false,
    });

    shipper.enqueue(makeEvent("evt-1"));
    shipper.enqueue(makeEvent("evt-2"));
    expect(shipper.flushWithBeacon()).toBe(false);

    shipper.enqueue(makeEvent("evt-3"));
    await shipper.flush();
    await shipper.close();

    expect(shipper.droppedEvents).toBe(1);
    expect(sentBodies).toHaveLength(1);
    expect(sentBodies[0]).toContain("evt-2");
    expect(sentBodies[0]).toContain("evt-3");
  });
});

function captureTransport(capturedBatches: InferenceEvent[][]): FetchLike {
  return async (_input, init) => {
    const body = String(init?.body ?? "");
    const payload = JSON.parse(body) as { events: InferenceEvent[] };
    capturedBatches.push(payload.events);
    return new Response(JSON.stringify({ accepted: payload.events.length }), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });
  };
}

class FakeUnloadTarget {
  private readonly listeners = new Map<string, Array<() => void>>();

  addEventListener(type: string, listener: () => void): void {
    const current = this.listeners.get(type) ?? [];
    current.push(listener);
    this.listeners.set(type, current);
  }

  dispatch(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener();
    }
  }
}

async function* streamChunks(chunks: unknown[]): AsyncIterable<unknown> {
  for (const chunk of chunks) {
    yield chunk;
  }
}

function makeEvent(eventId: string): InferenceEvent {
  return {
    event_id: eventId,
    event_schema_version: 1,
    ts: new Date().toISOString(),
    conversation_id: `conv-${eventId}`,
    user_id: null,
    client: "js-sdk",
    sdk_version: "0.1.0",
    provider: "openai",
    model: "gpt-4.1-mini",
    request: {
      messages_preview: "user: hello",
      stream: false,
      temperature: null,
      max_tokens: null,
    },
    response: {
      text_preview: "assistant: hi",
      finish_reason: "stop",
    },
    usage: {
      prompt_tokens: 1,
      completion_tokens: 1,
      total_tokens: 2,
    },
    timing: {
      latency_ms: 1,
      ttft_ms: 0,
    },
    status: "ok",
    error: null,
    cost_usd: 0,
    request_id: `req-${eventId}`,
    extra: {},
  };
}
