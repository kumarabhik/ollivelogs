function randomHex(length: number): string {
  const bytes = crypto.getRandomValues(new Uint8Array(length / 2));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function nextTraceparent(): string {
  return `00-${randomHex(32)}-${randomHex(16)}-01`;
}

export function nextRequestId(): string {
  return `req_${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
}

export function withTelemetryHeaders(
  headers?: HeadersInit,
  overrides?: {
    traceparent?: string;
    requestId?: string;
  },
): Headers {
  const next = new Headers(headers);
  if (!next.has("traceparent")) {
    next.set("traceparent", overrides?.traceparent ?? nextTraceparent());
  }
  if (!next.has("x-request-id")) {
    next.set("x-request-id", overrides?.requestId ?? nextRequestId());
  }
  return next;
}
