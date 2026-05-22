import { afterEach, describe, expect, it, vi } from "vitest";

import { OlliveLogs } from "../src";
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
