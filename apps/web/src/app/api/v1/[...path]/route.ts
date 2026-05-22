import { NextRequest } from "next/server";

import { handleMockApi } from "@/lib/mock-chat-api";

export const dynamic = "force-dynamic";

const PROXY_TARGET = process.env.CHAT_API_PROXY_TARGET;

export async function GET(
  request: NextRequest,
  context: { params: { path: string[] } },
): Promise<Response> {
  return route(request, context.params.path);
}

export async function POST(
  request: NextRequest,
  context: { params: { path: string[] } },
): Promise<Response> {
  return route(request, context.params.path);
}

export async function PATCH(
  request: NextRequest,
  context: { params: { path: string[] } },
): Promise<Response> {
  return route(request, context.params.path);
}

export async function DELETE(
  request: NextRequest,
  context: { params: { path: string[] } },
): Promise<Response> {
  return route(request, context.params.path);
}

async function route(request: NextRequest, path: string[]): Promise<Response> {
  if (PROXY_TARGET !== undefined && PROXY_TARGET !== "") {
    return proxyToChatApi(request, path);
  }
  return handleMockApi(request, path);
}

async function proxyToChatApi(request: NextRequest, path: string[]): Promise<Response> {
  const url = new URL(`/v1/${path.join("/")}`, PROXY_TARGET);
  const headers = new Headers(request.headers);
  headers.set("accept", request.headers.get("accept") ?? "*/*");
  const methodAllowsBody = !["GET", "HEAD"].includes(request.method);
  const body = methodAllowsBody ? await request.text() : undefined;
  const upstream = await fetch(url, {
    method: request.method,
    headers,
    body,
  });

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    responseHeaders.set(key, value);
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
