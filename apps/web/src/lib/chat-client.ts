import type {
  ConversationDetail,
  ConversationListResponse,
  ConversationSummary,
  SseCancelledEvent,
  SseDoneEvent,
  SseErrorEvent,
  SseStartEvent,
  SseTokenEvent,
} from "@/lib/chat-types";
import { withTelemetryHeaders } from "@/lib/telemetry";

export async function listConversations(): Promise<ConversationListResponse> {
  return getJson<ConversationListResponse>("/api/v1/conversations");
}

export async function createConversation(payload?: {
  title?: string | null;
  model_default?: string | null;
}): Promise<ConversationSummary> {
  return sendJson<ConversationSummary>("/api/v1/conversations", "POST", payload ?? {});
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  return getJson<ConversationDetail>(`/api/v1/conversations/${id}`);
}

export async function renameConversation(
  id: string,
  title: string,
): Promise<ConversationSummary> {
  return sendJson<ConversationSummary>(`/api/v1/conversations/${id}`, "PATCH", { title });
}

export async function deleteConversation(id: string): Promise<void> {
  await sendJson(`/api/v1/conversations/${id}`, "DELETE");
}

export async function cancelConversation(id: string): Promise<void> {
  await sendJson(`/api/v1/conversations/${id}/cancel`, "POST");
}

export async function streamMessage(
  conversationId: string,
  payload: {
    content: string;
    provider: string;
    model: string;
  },
  handlers: {
    onStart?(event: SseStartEvent): void;
    onToken?(event: SseTokenEvent): void;
    onDone?(event: SseDoneEvent): void;
    onCancelled?(event: SseCancelledEvent): void;
    onError?(event: SseErrorEvent): void;
  },
): Promise<void> {
  const response = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: withTelemetryHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({
      content: payload.content,
      provider: payload.provider,
      model: payload.model,
      stream: true,
    }),
  });

  if (!response.ok || response.body === null) {
    throw new Error(await extractError(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;

  while (!finished) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const parsed = parseEvent(part);
      if (parsed === null) {
        continue;
      }
      if (parsed.event === "start") {
        handlers.onStart?.(parsed.payload as SseStartEvent);
      } else if (parsed.event === "token") {
        handlers.onToken?.(parsed.payload as SseTokenEvent);
      } else if (parsed.event === "done") {
        handlers.onDone?.(parsed.payload as SseDoneEvent);
        finished = true;
      } else if (parsed.event === "cancelled") {
        handlers.onCancelled?.(parsed.payload as SseCancelledEvent);
        finished = true;
      } else if (parsed.event === "error") {
        handlers.onError?.(parsed.payload as SseErrorEvent);
        finished = true;
      }
    }
  }
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, {
    cache: "no-store",
    headers: withTelemetryHeaders(),
  });
  if (!response.ok) {
    throw new Error(await extractError(response));
  }
  return (await response.json()) as T;
}

async function sendJson<T = void>(
  url: string,
  method: "POST" | "PATCH" | "DELETE",
  body?: object,
): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: withTelemetryHeaders({
      "Content-Type": "application/json",
    }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await extractError(response));
  }
  if (response.headers.get("content-length") === "0" || response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function parseEvent(chunk: string): { event: string; payload: object } | null {
  const lines = chunk.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event: "));
  const dataLine = lines.find((line) => line.startsWith("data: "));
  if (eventLine === undefined || dataLine === undefined) {
    return null;
  }
  return {
    event: eventLine.slice("event: ".length),
    payload: JSON.parse(dataLine.slice("data: ".length)) as object,
  };
}

async function extractError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `Request failed with ${response.status}`;
  } catch {
    return `Request failed with ${response.status}`;
  }
}
