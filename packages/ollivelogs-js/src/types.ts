export interface EventContext {
  conversationId: string;
  userId?: string;
  provider?: string;
  model?: string;
  clientName?: string;
}

export interface OlliveLogsOptions {
  endpoint: string;
  sdkVersion?: string;
  clientName?: string;
  batchSize?: number;
  flushIntervalMs?: number;
  queueMax?: number;
  maxRetries?: number;
  defaultContext?: Partial<EventContext>;
  fetch?: FetchLike;
  sendBeacon?: SendBeaconLike;
  unloadTarget?: UnloadTarget;
}

export interface InferenceRequest {
  messages_preview: string | null;
  stream: boolean | null;
  temperature: number | null;
  max_tokens: number | null;
}

export interface InferenceResponse {
  text_preview: string | null;
  finish_reason: string | null;
}

export interface InferenceUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface InferenceTiming {
  latency_ms: number;
  ttft_ms: number;
}

export interface InferenceError {
  kind: string;
  message: string;
}

export interface InferenceEvent {
  event_id: string;
  event_schema_version: number;
  ts: string;
  conversation_id: string;
  user_id: string | null;
  client: string;
  sdk_version: string;
  provider: string;
  model: string;
  request: InferenceRequest;
  response: InferenceResponse;
  usage: InferenceUsage;
  timing: InferenceTiming;
  status: "ok" | "error";
  error: InferenceError | null;
  cost_usd: number;
  request_id: string;
  extra: Record<string, unknown>;
}

export type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export type SendBeaconLike = (url: string, data: BodyInit) => boolean;

export interface UnloadTarget {
  addEventListener(type: string, listener: () => void): void;
}

export interface ChatCompletionMessage {
  role?: string;
  content?: string | Array<{ text?: string; type?: string }>;
}

export interface OpenAIChatCompletionRequest {
  model?: string;
  messages?: ChatCompletionMessage[];
  stream?: boolean;
  temperature?: number;
  max_tokens?: number;
  max_completion_tokens?: number;
}

export interface OpenAIChatCompletionResponse {
  choices?: Array<{
    message?: { content?: string | Array<{ text?: string }> };
    delta?: { content?: string | Array<{ text?: string }> };
    finish_reason?: string | null;
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

export interface OpenAIClientLike {
  chat: {
    completions: {
      create(
        request: OpenAIChatCompletionRequest,
        ...rest: unknown[]
      ): Promise<unknown> | AsyncIterable<unknown> | unknown;
    };
  };
}
