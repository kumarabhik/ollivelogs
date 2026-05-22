export type ConversationStatus = "active" | "cancelled" | "archived";
export type MessageRole = "system" | "user" | "assistant" | "tool";
export type MessageStatus = "ok" | "error" | "cancelled" | "partial";

export interface MessageResource {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  token_count: number | null;
  status: MessageStatus;
  event_id: string | null;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  user_id: string | null;
  title: string | null;
  status: ConversationStatus;
  model_default: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: MessageResource[];
}

export interface ConversationListResponse {
  items: ConversationSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface SendMessageRequest {
  content: string;
  provider?: string | null;
  model?: string | null;
  stream: boolean;
}

export interface ConversationStatusResponse {
  conversation_id: string;
  status: ConversationStatus | "cancel_requested";
}

export interface SendMessageResponse {
  conversation_id: string;
  provider: string;
  model: string;
  context_turns: number;
  user_message: MessageResource;
  assistant_message: MessageResource;
}

export interface SseStartEvent {
  conversation_id: string;
  provider: string;
  model: string;
  context_turns: number;
  user_message_id: string;
}

export interface SseTokenEvent {
  delta: string;
}

export interface SseDoneEvent {
  message: MessageResource;
}

export interface SseCancelledEvent {
  message: MessageResource;
  reason: string;
}

export interface SseErrorEvent {
  message: MessageResource;
  reason: string;
}

export interface UiMessage extends MessageResource {
  streaming?: boolean;
  optimistic?: boolean;
}
