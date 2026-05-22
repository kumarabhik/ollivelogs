import { NextResponse } from "next/server";

import type {
  ConversationDetail,
  ConversationListResponse,
  ConversationStatus,
  ConversationStatusResponse,
  ConversationSummary,
  MessageResource,
  MessageStatus,
  SendMessageRequest,
  SendMessageResponse,
  SseCancelledEvent,
  SseDoneEvent,
  SseErrorEvent,
  SseStartEvent,
  SseTokenEvent,
} from "@/lib/chat-types";

const SESSION_COOKIE_NAME = "ollive_session";
const DEFAULT_MODEL = "gpt-4.1-mini";
const DEFAULT_PROVIDER = "openai";
const STREAM_DELAY_MS = 48;
const sessions = new Map<string, MockSession>();

interface MockSession {
  sessionId: string;
  userId: string;
  conversations: Map<string, MockConversation>;
}

interface MockConversation {
  id: string;
  title: string | null;
  status: ConversationStatus;
  modelDefault: string | null;
  createdAt: string;
  updatedAt: string;
  messages: MessageResource[];
  cancelRequested: boolean;
}

interface MockRequestContext {
  sessionId: string;
  session: MockSession;
}

export async function handleMockApi(
  request: Request,
  pathSegments: string[],
): Promise<Response> {
  const context = getSessionContext(request);
  if (pathSegments.length === 1 && pathSegments[0] === "conversations") {
    if (request.method === "GET") {
      return withSessionCookie(
        NextResponse.json(listConversations(context.session)),
        context.sessionId,
      );
    }
    if (request.method === "POST") {
      const payload = (await request.json()) as {
        title?: string | null;
        model_default?: string | null;
      };
      return withSessionCookie(
        NextResponse.json(
          createConversation(
            context.session,
            payload.title?.trim() || null,
            payload.model_default?.trim() || null,
          ),
        ),
        context.sessionId,
      );
    }
  }

  if (pathSegments.length === 2 && pathSegments[0] === "conversations") {
    const conversationId = pathSegments[1];
    if (conversationId === undefined) {
      return NextResponse.json({ detail: "Conversation not found" }, { status: 404 });
    }

    if (request.method === "GET") {
      const detail = getConversationDetail(context.session, conversationId);
      if (detail === null) {
        return NextResponse.json({ detail: "Conversation not found" }, { status: 404 });
      }
      return withSessionCookie(NextResponse.json(detail), context.sessionId);
    }
    if (request.method === "DELETE") {
      const archived = archiveConversation(context.session, conversationId);
      if (archived === null) {
        return NextResponse.json({ detail: "Conversation not found" }, { status: 404 });
      }
      return withSessionCookie(NextResponse.json(archived), context.sessionId);
    }
    if (request.method === "PATCH") {
      const payload = (await request.json()) as { title?: string | null };
      const renamed = renameConversation(context.session, conversationId, payload.title ?? null);
      if (renamed === null) {
        return NextResponse.json({ detail: "Conversation not found" }, { status: 404 });
      }
      return withSessionCookie(NextResponse.json(renamed), context.sessionId);
    }
  }

  if (
    pathSegments.length === 3 &&
    pathSegments[0] === "conversations" &&
    pathSegments[2] === "cancel"
  ) {
    const conversationId = pathSegments[1];
    if (conversationId === undefined) {
      return NextResponse.json({ detail: "Conversation not found" }, { status: 404 });
    }
    const cancelled = requestCancel(context.session, conversationId);
    if (cancelled === null) {
      return NextResponse.json({ detail: "Conversation not found" }, { status: 404 });
    }
    return withSessionCookie(NextResponse.json(cancelled), context.sessionId);
  }

  if (
    pathSegments.length === 3 &&
    pathSegments[0] === "conversations" &&
    pathSegments[2] === "messages" &&
    request.method === "POST"
  ) {
    const conversationId = pathSegments[1];
    if (conversationId === undefined) {
      return NextResponse.json({ detail: "Conversation not found" }, { status: 404 });
    }
    const payload = (await request.json()) as SendMessageRequest;
    const response = await sendMessage(context, conversationId, payload);
    return withSessionCookie(response, context.sessionId);
  }

  return NextResponse.json({ detail: "Not found" }, { status: 404 });
}

function getSessionContext(request: Request): MockRequestContext {
  const cookieHeader = request.headers.get("cookie") ?? "";
  const existingSessionId = getCookieValue(cookieHeader, SESSION_COOKIE_NAME);
  if (existingSessionId !== null && sessions.has(existingSessionId)) {
    return {
      sessionId: existingSessionId,
      session: sessions.get(existingSessionId)!,
    };
  }

  const sessionId = `demo_${crypto.randomUUID()}`;
  const session: MockSession = {
    sessionId,
    userId: crypto.randomUUID(),
    conversations: new Map<string, MockConversation>(),
  };
  sessions.set(sessionId, session);
  return { sessionId, session };
}

function listConversations(session: MockSession): ConversationListResponse {
  const items = [...session.conversations.values()]
    .filter((conversation) => conversation.status !== "archived")
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .map(toConversationSummary(session.userId));
  return {
    items,
    total: items.length,
    limit: 50,
    offset: 0,
  };
}

function createConversation(
  session: MockSession,
  title: string | null,
  modelDefault: string | null,
): ConversationSummary {
  const now = new Date().toISOString();
  const conversation: MockConversation = {
    id: crypto.randomUUID(),
    title,
    status: "active",
    modelDefault: modelDefault ?? DEFAULT_MODEL,
    createdAt: now,
    updatedAt: now,
    messages: [],
    cancelRequested: false,
  };
  session.conversations.set(conversation.id, conversation);
  return toConversationSummary(session.userId)(conversation);
}

function getConversationDetail(
  session: MockSession,
  conversationId: string,
): ConversationDetail | null {
  const conversation = session.conversations.get(conversationId);
  if (conversation === undefined || conversation.status === "archived") {
    return null;
  }
  return {
    ...toConversationSummary(session.userId)(conversation),
    messages: conversation.messages,
  };
}

function archiveConversation(
  session: MockSession,
  conversationId: string,
): ConversationStatusResponse | null {
  const conversation = session.conversations.get(conversationId);
  if (conversation === undefined || conversation.status === "archived") {
    return null;
  }
  conversation.status = "archived";
  conversation.updatedAt = new Date().toISOString();
  return {
    conversation_id: conversation.id,
    status: "archived",
  };
}

function renameConversation(
  session: MockSession,
  conversationId: string,
  title: string | null,
): ConversationSummary | null {
  const conversation = session.conversations.get(conversationId);
  if (conversation === undefined || conversation.status === "archived") {
    return null;
  }
  conversation.title = title?.trim() || "Untitled conversation";
  conversation.updatedAt = new Date().toISOString();
  return toConversationSummary(session.userId)(conversation);
}

function requestCancel(
  session: MockSession,
  conversationId: string,
): ConversationStatusResponse | null {
  const conversation = session.conversations.get(conversationId);
  if (conversation === undefined || conversation.status === "archived") {
    return null;
  }
  conversation.cancelRequested = true;
  conversation.status = "cancelled";
  conversation.updatedAt = new Date().toISOString();
  return {
    conversation_id: conversation.id,
    status: "cancel_requested",
  };
}

async function sendMessage(
  context: MockRequestContext,
  conversationId: string,
  payload: SendMessageRequest,
): Promise<Response> {
  const conversation = context.session.conversations.get(conversationId);
  if (conversation === undefined || conversation.status === "archived") {
    return NextResponse.json({ detail: "Conversation not found" }, { status: 404 });
  }

  conversation.cancelRequested = false;
  conversation.status = "active";
  const userMessage = pushMessage(conversation, {
    role: "user",
    content: payload.content,
    status: "ok",
    token_count: estimateTokenCount(payload.content),
  });
  const provider = payload.provider?.trim() || DEFAULT_PROVIDER;
  const model = payload.model?.trim() || conversation.modelDefault || DEFAULT_MODEL;

  if (!payload.stream) {
    const assistantMessage = pushMessage(conversation, {
      role: "assistant",
      content: buildAssistantReply(payload.content, provider, model, 0),
      status: "ok",
      token_count: estimateTokenCount(payload.content) + 12,
    });
    const body: SendMessageResponse = {
      conversation_id: conversation.id,
      provider,
      model,
      context_turns: Math.ceil(conversation.messages.length / 2),
      user_message: userMessage,
      assistant_message: assistantMessage,
    };
    return NextResponse.json(body);
  }

  const encoder = new TextEncoder();
  const reply = buildAssistantReply(payload.content, provider, model, conversation.messages.length);
  const tokens = reply.split(" ");
  let emittedText = "";
  let controllerClosed = false;
  let clientDisconnected = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const enqueue = (chunk: string): void => {
        if (controllerClosed || clientDisconnected) {
          return;
        }
        try {
          controller.enqueue(encoder.encode(chunk));
        } catch (error: unknown) {
          if (isClosedStreamError(error)) {
            controllerClosed = true;
            clientDisconnected = true;
            return;
          }
          throw error;
        }
      };
      const close = (): void => {
        if (!controllerClosed) {
          try {
            controller.close();
          } catch (error: unknown) {
            if (!isClosedStreamError(error)) {
              throw error;
            }
          } finally {
            controllerClosed = true;
          }
        }
      };
      const startEvent: SseStartEvent = {
        conversation_id: conversation.id,
        provider,
        model,
        context_turns: Math.ceil(conversation.messages.length / 2),
        user_message_id: userMessage.id,
      };
      enqueue(formatSse("start", startEvent));

      void (async () => {
        try {
          for (const token of tokens) {
            await sleep(STREAM_DELAY_MS);
            if (conversation.cancelRequested) {
              const cancelledMessage = pushMessage(conversation, {
                role: "assistant",
                content: emittedText.trim(),
                status: "cancelled",
                token_count: estimateTokenCount(emittedText),
              });
              const cancelledEvent: SseCancelledEvent = {
                message: cancelledMessage,
                reason: "cancelled",
              };
              enqueue(formatSse("cancelled", cancelledEvent));
              close();
              return;
            }
            const delta = emittedText === "" ? token : ` ${token}`;
            emittedText += delta;
            const tokenEvent: SseTokenEvent = { delta };
            enqueue(formatSse("token", tokenEvent));
          }

          const doneMessage = pushMessage(conversation, {
            role: "assistant",
            content: emittedText.trim(),
            status: "ok",
            token_count: estimateTokenCount(emittedText),
          });
          const doneEvent: SseDoneEvent = { message: doneMessage };
          enqueue(formatSse("done", doneEvent));
          close();
        } catch (error: unknown) {
          const failedMessage = pushMessage(conversation, {
            role: "assistant",
            content: emittedText.trim(),
            status: "error",
            token_count: estimateTokenCount(emittedText),
          });
          const errorEvent: SseErrorEvent = {
            message: failedMessage,
            reason: error instanceof Error ? error.message : "stream_error",
          };
          enqueue(formatSse("error", errorEvent));
          close();
        } finally {
          conversation.cancelRequested = false;
        }
      })();
    },
    cancel() {
      clientDisconnected = true;
      controllerClosed = true;
      conversation.cancelRequested = true;
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}

function pushMessage(
  conversation: MockConversation,
  input: {
    role: MessageResource["role"];
    content: string;
    status: MessageStatus;
    token_count: number;
  },
): MessageResource {
  const message: MessageResource = {
    id: crypto.randomUUID(),
    conversation_id: conversation.id,
    role: input.role,
    content: input.content,
    token_count: input.token_count,
    status: input.status,
    event_id: crypto.randomUUID(),
    created_at: new Date().toISOString(),
  };
  conversation.messages = [...conversation.messages, message];
  conversation.updatedAt = message.created_at;
  if (conversation.title === null && input.role === "user") {
    conversation.title = input.content.slice(0, 42);
  }
  return message;
}

function toConversationSummary(userId: string) {
  return (conversation: MockConversation): ConversationSummary => ({
    id: conversation.id,
    user_id: userId,
    title: conversation.title,
    status: conversation.status,
    model_default: conversation.modelDefault,
    created_at: conversation.createdAt,
    updated_at: conversation.updatedAt,
  });
}

function buildAssistantReply(
  prompt: string,
  provider: string,
  model: string,
  historySize: number,
): string {
  return [
    `Layered insight from ${provider}/${model}.`,
    `I am tracking ${historySize} prior messages for this browser session.`,
    `You asked: "${prompt.trim()}".`,
    "The stream is chunked word by word so the interface can animate real progress.",
    "If you hit stop, I will persist only the partial assistant text and mark the run cancelled.",
  ].join(" ");
}

function estimateTokenCount(text: string): number {
  return text.trim() === "" ? 0 : text.trim().split(/\s+/).length;
}

function withSessionCookie<T extends Response>(response: T, sessionId: string): T {
  response.headers.append(
    "Set-Cookie",
    `${SESSION_COOKIE_NAME}=${sessionId}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000`,
  );
  return response;
}

function formatSse(eventName: string, payload: object): string {
  return `event: ${eventName}\ndata: ${JSON.stringify(payload)}\n\n`;
}

function getCookieValue(cookieHeader: string, name: string): string | null {
  const prefix = `${name}=`;
  for (const part of cookieHeader.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return trimmed.slice(prefix.length);
    }
  }
  return null;
}

function sleep(delayMs: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, delayMs);
  });
}

function isClosedStreamError(error: unknown): boolean {
  if (error instanceof TypeError && error.message.includes("Controller is already closed")) {
    return true;
  }
  return Boolean(
    typeof error === "object" &&
      error !== null &&
      "code" in error &&
      (error as { code?: string }).code === "ERR_INVALID_STATE",
  );
}
