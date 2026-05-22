import { BatchedEventShipper } from "./shipper";
import {
  EventContext,
  FetchLike,
  InferenceEvent,
  OlliveLogsOptions,
  OpenAIChatCompletionRequest,
  OpenAIChatCompletionResponse,
  SendBeaconLike,
} from "./types";

const OPENAI_CREATE_PATH = "chat.completions.create";

export class OlliveLogs {
  private readonly endpoint: string;
  private readonly sdkVersion: string;
  private readonly clientName: string;
  private readonly defaultContext: Partial<EventContext> | undefined;
  private readonly shipper: BatchedEventShipper;

  constructor(options: OlliveLogsOptions) {
    this.endpoint = options.endpoint;
    this.sdkVersion = options.sdkVersion ?? "0.1.0";
    this.clientName = options.clientName ?? "js-sdk";
    this.defaultContext = options.defaultContext;
    this.shipper = new BatchedEventShipper({
      endpoint: this.endpoint,
      batchSize: options.batchSize ?? 50,
      flushIntervalMs: options.flushIntervalMs ?? 200,
      queueMax: options.queueMax ?? 1000,
      maxRetries: options.maxRetries ?? 2,
      fetchImpl: options.fetch ?? resolveFetch(),
      sendBeacon: options.sendBeacon ?? resolveSendBeacon(),
    });
    registerUnloadHandlers(options.unloadTarget ?? resolveUnloadTarget(), this.shipper);
  }

  get droppedEvents(): number {
    return this.shipper.droppedEvents;
  }

  wrap<T extends object>(client: T, context?: Partial<EventContext>): T {
    return this.wrapObject(client, context, []);
  }

  wrapFetch(fetchImpl?: FetchLike, context?: Partial<EventContext>): FetchLike {
    const baseFetch = fetchImpl ?? resolveFetch();
    return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const resolvedContext = this.resolveContext(context);
      const startedAt = performance.now();
      const request = await buildFetchRequest(input, init);

      try {
        const response = await baseFetch(input, init);
        const responsePreview = truncate(await safeResponseText(response));
        this.shipper.enqueue(
          this.buildEvent({
            context: resolvedContext,
            provider: resolvedContext.provider ?? "fetch",
            model: resolvedContext.model ?? request.url,
            requestPreview: request.preview,
            responsePreview,
            stream: false,
            temperature: null,
            maxTokens: null,
            promptTokens: 0,
            completionTokens: 0,
            totalTokens: 0,
            finishReason: response.ok ? `http_${response.status}` : "http_error",
            status: response.ok ? "ok" : "error",
            latencyMs: elapsedMs(startedAt),
            error: response.ok
              ? null
              : {
                  kind: "FetchError",
                  message: `${request.method} ${request.url} returned ${response.status}`,
                },
            extra: {
              fetch_kind: "interceptor",
              method: request.method,
              url: request.url,
              response_status: response.status,
            },
          }),
        );
        return response;
      } catch (error: unknown) {
        this.shipper.enqueue(
          this.buildEvent({
            context: resolvedContext,
            provider: resolvedContext.provider ?? "fetch",
            model: resolvedContext.model ?? request.url,
            requestPreview: request.preview,
            responsePreview: "",
            stream: false,
            temperature: null,
            maxTokens: null,
            promptTokens: 0,
            completionTokens: 0,
            totalTokens: 0,
            finishReason: null,
            status: "error",
            latencyMs: elapsedMs(startedAt),
            error: normalizeError(error),
            extra: {
              fetch_kind: "interceptor",
              method: request.method,
              url: request.url,
            },
          }),
        );
        throw error;
      }
    };
  }

  async flush(): Promise<void> {
    await this.shipper.flush();
  }

  async close(): Promise<void> {
    await this.shipper.close();
  }

  private wrapObject<T extends object>(
    target: T,
    context: Partial<EventContext> | undefined,
    path: string[],
  ): T {
    return new Proxy(target, {
      get: (proxyTarget, property, receiver) => {
        const value = Reflect.get(proxyTarget, property, receiver);
        const nextPath = [...path, String(property)];
        if (typeof value === "function" && nextPath.join(".") === OPENAI_CREATE_PATH) {
          return (...args: unknown[]) => {
            return this.recordOpenAICall(
              value as (...callArgs: unknown[]) => unknown,
              proxyTarget,
              args,
              context,
            );
          };
        }
        if (typeof value === "object" && value !== null) {
          return this.wrapObject(value as object, context, nextPath);
        }
        return value;
      },
    }) as T;
  }

  private recordOpenAICall(
    invoke: (...args: unknown[]) => unknown,
    thisArg: object,
    args: unknown[],
    context: Partial<EventContext> | undefined,
  ): unknown {
    const resolvedContext = this.resolveContext(context);
    const request = isOpenAIRequest(args[0]) ? args[0] : undefined;
    const startedAt = performance.now();
    const result = invoke.apply(thisArg, args);

    if (isAsyncIterable(result)) {
      return this.wrapOpenAIStream(result, request, resolvedContext, startedAt);
    }

    if (isPromiseLike(result)) {
      return Promise.resolve(result)
        .then((resolved) => {
        if (isAsyncIterable(resolved)) {
          return this.wrapOpenAIStream(resolved, request, resolvedContext, startedAt);
        }
        this.enqueueOpenAIResult(resolved, request, resolvedContext, startedAt);
        return resolved;
        })
        .catch((error: unknown) => {
          this.enqueueOpenAIError(error, request, resolvedContext, startedAt);
          throw error;
        });
    }

    this.enqueueOpenAIResult(result, request, resolvedContext, startedAt);
    return result;
  }

  private async *wrapOpenAIStream(
    stream: AsyncIterable<unknown>,
    request: OpenAIChatCompletionRequest | undefined,
    context: EventContext,
    startedAt: number,
  ): AsyncIterable<unknown> {
    let textPreview = "";
    let finishReason: string | null = null;
    let promptTokens = 0;
    let completionTokens = 0;
    let totalTokens = 0;

    try {
      for await (const chunk of stream) {
        textPreview += extractStreamDelta(chunk);
        finishReason = extractFinishReason(chunk) ?? finishReason;
        promptTokens = extractPromptTokens(chunk) ?? promptTokens;
        completionTokens = extractCompletionTokens(chunk) ?? completionTokens;
        totalTokens = extractTotalTokens(chunk) ?? totalTokens;
        yield chunk;
      }

      this.shipper.enqueue(
        this.buildEvent({
          context,
          provider: context.provider ?? "openai",
          model: request?.model ?? context.model ?? "unknown",
          requestPreview: extractMessagesPreview(request?.messages),
          responsePreview: truncate(textPreview),
          stream: request?.stream ?? true,
          temperature: request?.temperature ?? null,
          maxTokens: request?.max_completion_tokens ?? request?.max_tokens ?? null,
          promptTokens,
          completionTokens,
          totalTokens: totalTokens || promptTokens + completionTokens,
          finishReason,
          status: "ok",
          latencyMs: elapsedMs(startedAt),
          error: null,
          extra: {
            trace_kind: "openai_wrapper_stream",
          },
        }),
      );
    } catch (error: unknown) {
      this.enqueueOpenAIError(error, request, context, startedAt);
      throw error;
    }
  }

  private enqueueOpenAIResult(
    result: unknown,
    request: OpenAIChatCompletionRequest | undefined,
    context: EventContext,
    startedAt: number,
  ): void {
    const response = isOpenAIResponse(result) ? result : undefined;
    const promptTokens = response?.usage?.prompt_tokens ?? 0;
    const completionTokens = response?.usage?.completion_tokens ?? 0;
    const totalTokens =
      response?.usage?.total_tokens ?? promptTokens + completionTokens;
    this.shipper.enqueue(
      this.buildEvent({
        context,
        provider: context.provider ?? "openai",
        model: request?.model ?? context.model ?? "unknown",
        requestPreview: extractMessagesPreview(request?.messages),
        responsePreview: truncate(extractResponseText(response)),
        stream: request?.stream ?? false,
        temperature: request?.temperature ?? null,
        maxTokens: request?.max_completion_tokens ?? request?.max_tokens ?? null,
        promptTokens,
        completionTokens,
        totalTokens,
        finishReason: extractFinishReason(response),
        status: "ok",
        latencyMs: elapsedMs(startedAt),
        error: null,
        extra: {
          trace_kind: "openai_wrapper",
        },
      }),
    );
  }

  private enqueueOpenAIError(
    error: unknown,
    request: OpenAIChatCompletionRequest | undefined,
    context: EventContext,
    startedAt: number,
  ): void {
    this.shipper.enqueue(
      this.buildEvent({
        context,
        provider: context.provider ?? "openai",
        model: request?.model ?? context.model ?? "unknown",
        requestPreview: extractMessagesPreview(request?.messages),
        responsePreview: "",
        stream: request?.stream ?? false,
        temperature: request?.temperature ?? null,
        maxTokens: request?.max_completion_tokens ?? request?.max_tokens ?? null,
        promptTokens: 0,
        completionTokens: 0,
        totalTokens: 0,
        finishReason: null,
        status: "error",
        latencyMs: elapsedMs(startedAt),
        error: normalizeError(error),
        extra: {
          trace_kind: "openai_wrapper",
        },
      }),
    );
  }

  private resolveContext(context?: Partial<EventContext>): EventContext {
    const merged: Partial<EventContext> = {
      ...this.defaultContext,
      ...context,
    };
    const conversationId = merged.conversationId;
    if (conversationId === undefined || conversationId.trim() === "") {
      throw new Error("OlliveLogs requires a conversationId in the default context or per call.");
    }
    const resolved: EventContext = { conversationId };
    if (merged.userId !== undefined) {
      resolved.userId = merged.userId;
    }
    if (merged.provider !== undefined) {
      resolved.provider = merged.provider;
    }
    if (merged.model !== undefined) {
      resolved.model = merged.model;
    }
    if (merged.clientName !== undefined) {
      resolved.clientName = merged.clientName;
    }
    return resolved;
  }

  private buildEvent(args: {
    context: EventContext;
    provider: string;
    model: string;
    requestPreview: string;
    responsePreview: string;
    stream: boolean;
    temperature: number | null;
    maxTokens: number | null;
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    finishReason: string | null;
    status: "ok" | "error";
    latencyMs: number;
    error: { kind: string; message: string } | null;
    extra: Record<string, unknown>;
  }): InferenceEvent {
    return {
      event_id: crypto.randomUUID(),
      event_schema_version: 1,
      ts: new Date().toISOString(),
      conversation_id: args.context.conversationId,
      user_id: args.context.userId ?? null,
      client: args.context.clientName ?? this.clientName,
      sdk_version: this.sdkVersion,
      provider: args.provider,
      model: args.model,
      request: {
        messages_preview: truncate(args.requestPreview) || null,
        stream: args.stream,
        temperature: args.temperature,
        max_tokens: args.maxTokens,
      },
      response: {
        text_preview: truncate(args.responsePreview) || null,
        finish_reason: args.finishReason,
      },
      usage: {
        prompt_tokens: args.promptTokens,
        completion_tokens: args.completionTokens,
        total_tokens: args.totalTokens,
      },
      timing: {
        latency_ms: args.latencyMs,
        ttft_ms: 0,
      },
      status: args.status,
      error: args.error,
      cost_usd: 0,
      request_id: `req_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`,
      extra: args.extra,
    };
  }
}

function registerUnloadHandlers(
  unloadTarget: { addEventListener(type: string, listener: () => void): void } | undefined,
  shipper: BatchedEventShipper,
): void {
  if (unloadTarget === undefined) {
    return;
  }
  const flush = (): void => {
    shipper.flushWithBeacon();
  };
  unloadTarget.addEventListener("pagehide", flush);
  unloadTarget.addEventListener("beforeunload", flush);
}

function resolveFetch(): FetchLike {
  if (typeof fetch !== "function") {
    throw new Error("No fetch implementation available. Pass options.fetch when constructing OlliveLogs.");
  }
  return fetch.bind(globalThis);
}

function resolveSendBeacon(): SendBeaconLike | undefined {
  const navigatorCandidate = globalThis.navigator as Navigator | undefined;
  if (navigatorCandidate !== undefined && typeof navigatorCandidate.sendBeacon === "function") {
    return navigatorCandidate.sendBeacon.bind(navigatorCandidate);
  }
  return undefined;
}

function resolveUnloadTarget(): { addEventListener(type: string, listener: () => void): void } | undefined {
  if (typeof globalThis.addEventListener === "function") {
    return globalThis;
  }
  return undefined;
}

async function buildFetchRequest(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<{ method: string; url: string; preview: string }> {
  const method = init?.method ?? (input instanceof Request ? input.method : "GET");
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const bodyPreview = await safeRequestBody(input, init);
  return {
    method,
    url,
    preview: truncate(bodyPreview === "" ? `${method} ${url}` : bodyPreview),
  };
}

async function safeRequestBody(input: RequestInfo | URL, init?: RequestInit): Promise<string> {
  if (typeof init?.body === "string") {
    return init.body;
  }
  if (init?.body instanceof URLSearchParams) {
    return init.body.toString();
  }
  if (input instanceof Request) {
    try {
      return await input.clone().text();
    } catch {
      return "";
    }
  }
  return "";
}

async function safeResponseText(response: Response): Promise<string> {
  try {
    return await response.clone().text();
  } catch {
    return "";
  }
}

function isPromiseLike(value: unknown): value is PromiseLike<unknown> {
  return typeof value === "object" && value !== null && "then" in value && typeof value.then === "function";
}

function isAsyncIterable(value: unknown): value is AsyncIterable<unknown> {
  return typeof value === "object" && value !== null && Symbol.asyncIterator in value;
}

function isOpenAIRequest(value: unknown): value is OpenAIChatCompletionRequest {
  return typeof value === "object" && value !== null;
}

function isOpenAIResponse(value: unknown): value is OpenAIChatCompletionResponse {
  return typeof value === "object" && value !== null;
}

function extractMessagesPreview(messages: OpenAIChatCompletionRequest["messages"]): string {
  if (messages === undefined) {
    return "";
  }
  const parts: string[] = [];
  for (const message of messages) {
    const role = message.role ?? "user";
    if (typeof message.content === "string") {
      parts.push(`${role}: ${message.content}`);
      continue;
    }
    if (Array.isArray(message.content)) {
      const blocks = message.content
        .map((block) => block.text ?? "")
        .filter((text) => text !== "")
        .join("");
      if (blocks !== "") {
        parts.push(`${role}: ${blocks}`);
      }
    }
  }
  return parts.join("\n");
}

function extractResponseText(response: OpenAIChatCompletionResponse | undefined): string {
  const content = response?.choices?.[0]?.message?.content;
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content.map((item) => item.text ?? "").join("");
  }
  return "";
}

function extractStreamDelta(chunk: unknown): string {
  if (!isOpenAIResponse(chunk)) {
    return "";
  }
  const content = chunk.choices?.[0]?.delta?.content;
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content.map((item) => item.text ?? "").join("");
  }
  return "";
}

function extractFinishReason(response: unknown): string | null {
  if (!isOpenAIResponse(response)) {
    return null;
  }
  return response.choices?.[0]?.finish_reason ?? null;
}

function extractPromptTokens(response: unknown): number | undefined {
  if (!isOpenAIResponse(response)) {
    return undefined;
  }
  return response.usage?.prompt_tokens;
}

function extractCompletionTokens(response: unknown): number | undefined {
  if (!isOpenAIResponse(response)) {
    return undefined;
  }
  return response.usage?.completion_tokens;
}

function extractTotalTokens(response: unknown): number | undefined {
  if (!isOpenAIResponse(response)) {
    return undefined;
  }
  return response.usage?.total_tokens;
}

function elapsedMs(startedAt: number): number {
  return Math.max(0, Math.round(performance.now() - startedAt));
}

function normalizeError(error: unknown): { kind: string; message: string } {
  if (error instanceof Error) {
    return {
      kind: error.name,
      message: error.message,
    };
  }
  return {
    kind: "Error",
    message: String(error),
  };
}

function truncate(value: string, limit = 512): string {
  if (value.length <= limit) {
    return value;
  }
  return `${value.slice(0, limit - 3)}...`;
}
