# ollivelogs-js

Thin JavaScript SDK for OlliveLogs.

## Quick start

```ts
import OpenAI from "openai";
import { OlliveLogs } from "ollivelogs-js";

const ol = new OlliveLogs({
  endpoint: "http://localhost:8002/v1/logs",
  defaultContext: {
    conversationId: "conv-123",
    userId: "user-123",
  },
});

const openai = ol.wrap(new OpenAI({ apiKey: process.env.OPENAI_API_KEY }));
await openai.chat.completions.create({
  model: "gpt-4.1-mini",
  messages: [{ role: "user", content: "Hello" }],
});

await ol.flush();
await ol.close();
```

## What it does

- Wraps OpenAI-style `chat.completions.create(...)` calls and records request previews, response previews, usage, latency, and status.
- Wraps any `fetch` implementation for browser or Node usage.
- Ships events to OlliveLogs ingest in async batches and falls back to `sendBeacon` on unload when available.
- Exposes ESM, CJS, and TypeScript types from a single package.
