"use client";

import { startTransition, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Pencil,
  Plus,
  Square,
  Trash2,
} from "lucide-react";

import {
  cancelConversation,
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
  streamMessage,
} from "@/lib/chat-client";
import type {
  ConversationDetail,
  ConversationSummary,
  MessageResource,
  SseCancelledEvent,
  SseDoneEvent,
  UiMessage,
} from "@/lib/chat-types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

const providerOptions = ["openai", "anthropic", "gemini", "deepseek", "grok", "huggingface"];
const modelOptions = [
  "gpt-4.1-mini",
  "claude-sonnet-4-20250514",
  "gemini-2.5-flash",
  "deepseek-chat",
  "grok-3-mini",
  "Qwen/Qwen2.5-72B-Instruct",
];

export function ChatWorkspace() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [composer, setComposer] = useState("");
  const [provider, setProvider] = useState(providerOptions[0]!);
  const [model, setModel] = useState(modelOptions[0]!);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [loadingSidebar, setLoadingSidebar] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [sending, setSending] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  useEffect(() => {
    void refreshConversations();
  }, []);

  const messages = useMemo<UiMessage[]>(() => {
    return detail?.messages ?? [];
  }, [detail]);

  async function refreshConversations(preferredId?: string | null): Promise<void> {
    setLoadingSidebar(true);
    setGlobalError(null);
    try {
      const response = await listConversations();
      setConversations(response.items);
      const nextSelectedId =
        preferredId ??
        selectedConversationId ??
        response.items[0]?.id ??
        null;
      setSelectedConversationId(nextSelectedId);
      if (nextSelectedId !== null) {
        await loadConversation(nextSelectedId);
      } else {
        setDetail(null);
      }
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Failed to load conversations.");
    } finally {
      setLoadingSidebar(false);
    }
  }

  async function loadConversation(conversationId: string): Promise<void> {
    setLoadingDetail(true);
    try {
      const nextDetail = await getConversation(conversationId);
      setDetail(nextDetail);
      setSelectedConversationId(conversationId);
      if (nextDetail.model_default) {
        setModel(nextDetail.model_default);
      }
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Failed to open conversation.");
    } finally {
      setLoadingDetail(false);
    }
  }

  async function handleCreateConversation(): Promise<void> {
    setCreatingConversation(true);
    setGlobalError(null);
    try {
      const created = await createConversation({
        model_default: model,
      });
      setConversations((current) => [created, ...current]);
      await loadConversation(created.id);
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Failed to create conversation.");
    } finally {
      setCreatingConversation(false);
    }
  }

  async function handleRenameConversation(conversationId: string): Promise<void> {
    if (renameDraft.trim() === "") {
      return;
    }
    try {
      const renamed = await renameConversation(conversationId, renameDraft.trim());
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === conversationId ? renamed : conversation,
        ),
      );
      setDetail((current) =>
        current?.id === conversationId ? { ...current, title: renamed.title } : current,
      );
      setRenamingId(null);
      setRenameDraft("");
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Rename failed.");
    }
  }

  async function handleDeleteConversation(conversationId: string): Promise<void> {
    try {
      await deleteConversation(conversationId);
      const remaining = conversations.filter((conversation) => conversation.id !== conversationId);
      setConversations(remaining);
      if (selectedConversationId === conversationId) {
        const nextId = remaining[0]?.id ?? null;
        setSelectedConversationId(nextId);
        if (nextId !== null) {
          await loadConversation(nextId);
        } else {
          setDetail(null);
        }
      }
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Delete failed.");
    }
  }

  async function handleSendMessage(): Promise<void> {
    const trimmed = composer.trim();
    if (trimmed === "" || sending) {
      return;
    }
    setGlobalError(null);
    setSending(true);

    let conversationId = selectedConversationId;
    if (conversationId === null) {
      const created = await createConversation({
        model_default: model,
      });
      setConversations((current) => [created, ...current]);
      conversationId = created.id;
      setSelectedConversationId(created.id);
      setDetail({
        ...created,
        messages: [],
      });
    }

    const now = new Date().toISOString();
    const optimisticUser: UiMessage = {
      id: `temp-user-${crypto.randomUUID()}`,
      conversation_id: conversationId,
      role: "user",
      content: trimmed,
      token_count: trimmed.split(/\s+/).length,
      status: "ok",
      event_id: null,
      created_at: now,
      optimistic: true,
    };
    const optimisticAssistant: UiMessage = {
      id: `temp-assistant-${crypto.randomUUID()}`,
      conversation_id: conversationId,
      role: "assistant",
      content: "",
      token_count: null,
      status: "partial",
      event_id: null,
      created_at: now,
      streaming: true,
      optimistic: true,
    };
    setComposer("");
    setDetail((current) =>
      current === null
        ? null
        : {
            ...current,
            messages: [...current.messages, optimisticUser, optimisticAssistant],
          },
    );
    touchConversation(conversationId, trimmed);

    try {
      await streamMessage(
        conversationId,
        { content: trimmed, provider, model },
        {
          onToken: (event) => {
            setDetail((current) =>
              current === null
                ? null
                : {
                    ...current,
                    messages: current.messages.map((message) =>
                      message.id === optimisticAssistant.id
                        ? {
                            ...message,
                            content: `${message.content}${event.delta}`,
                          }
                        : message,
                    ),
                  },
            );
          },
          onDone: (event) => {
            commitFinalAssistantMessage(optimisticAssistant.id, event);
          },
          onCancelled: (event) => {
            commitCancelledAssistantMessage(optimisticAssistant.id, event);
          },
          onError: (event) => {
            commitErroredAssistantMessage(optimisticAssistant.id, event);
          },
        },
      );
      await refreshSidebarOnly(conversationId);
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Streaming failed.");
      setDetail((current) =>
        current === null
          ? null
          : {
              ...current,
              messages: current.messages.map((message) =>
                message.id === optimisticAssistant.id
                  ? { ...message, status: "error", streaming: false }
                  : message,
              ),
            },
      );
    } finally {
      setSending(false);
    }
  }

  async function handleStop(): Promise<void> {
    if (selectedConversationId === null) {
      return;
    }
    try {
      await cancelConversation(selectedConversationId);
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Cancel request failed.");
    }
  }

  async function refreshSidebarOnly(preferredId: string): Promise<void> {
    const response = await listConversations();
    setConversations(response.items);
    const selected = response.items.find((item) => item.id === preferredId);
    if (selected !== undefined) {
      setDetail((current) =>
        current?.id === preferredId
          ? {
              ...current,
              title: selected.title,
              status: selected.status,
              updated_at: selected.updated_at,
            }
          : current,
      );
    }
  }

  function commitFinalAssistantMessage(
    optimisticAssistantId: string,
    event: SseDoneEvent,
  ): void {
    setDetail((current) =>
      current === null
        ? null
        : {
            ...current,
            messages: current.messages.map((message) =>
              message.id === optimisticAssistantId ? event.message : message,
            ),
          },
    );
  }

  function commitCancelledAssistantMessage(
    optimisticAssistantId: string,
    event: SseCancelledEvent,
  ): void {
    setDetail((current) =>
      current === null
        ? null
        : {
            ...current,
            status: "cancelled",
            messages: current.messages.map((message) =>
              message.id === optimisticAssistantId ? event.message : message,
            ),
          },
    );
  }

  function commitErroredAssistantMessage(
    optimisticAssistantId: string,
    event: { message: MessageResource; reason: string },
  ): void {
    setGlobalError(event.reason);
    setDetail((current) =>
      current === null
        ? null
        : {
            ...current,
            messages: current.messages.map((message) =>
              message.id === optimisticAssistantId ? event.message : message,
            ),
          },
    );
  }

  function touchConversation(conversationId: string, fallbackTitle: string): void {
    startTransition(() => {
      setConversations((current) => {
        const updated = current.map((conversation) =>
          conversation.id === conversationId
            ? {
                ...conversation,
                title: conversation.title ?? fallbackTitle.slice(0, 42),
                updated_at: new Date().toISOString(),
              }
            : conversation,
        );
        return [...updated].sort((left, right) =>
          right.updated_at.localeCompare(left.updated_at),
        );
      });
    });
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-7xl flex-col gap-4 px-4 py-6 md:px-6 lg:py-8">
      <Card className="overflow-hidden bg-haze">
        <CardContent className="flex flex-col gap-3 p-5 md:flex-row md:items-end md:justify-between">
          <div className="space-y-2">
            <Badge className="bg-primary/15 text-primary">Anonymous demo session</Badge>
            <h1 className="font-display text-3xl font-semibold tracking-tight">
              OlliveLogs Conversation Console
            </h1>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Multi-provider chat, browser-streamed SSE, and cancellation that shows its work.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge className="font-mono">{provider}</Badge>
            <Badge className="font-mono">{model}</Badge>
          </div>
        </CardContent>
      </Card>

      {globalError !== null ? (
        <Card className="border-destructive/30 bg-destructive/8">
          <CardContent className="flex items-start gap-3 p-4 text-sm">
            <AlertCircle className="mt-0.5 h-4 w-4 text-destructive" />
            <div className="space-y-1">
              <p className="font-medium text-destructive">Something needs attention</p>
              <p className="text-muted-foreground">{globalError}</p>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="overflow-hidden">
          <CardHeader className="border-b border-white/8">
            <div className="flex items-center justify-between gap-3">
              <div>
                <CardTitle>Conversations</CardTitle>
                <CardDescription>Resume, rename, delete, or create a fresh thread.</CardDescription>
              </div>
              <Button
                onClick={() => void handleCreateConversation()}
                disabled={creatingConversation}
                size="sm"
              >
                {creatingConversation ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Plus className="h-4 w-4" />
                )}
                New
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-3">
            {loadingSidebar ? (
              <div className="space-y-3">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : conversations.length === 0 ? (
              <div className="rounded-[24px] border border-dashed border-white/10 p-5 text-sm text-muted-foreground">
                No conversations yet. Create one to see the browser session cookie and stream parser come alive.
              </div>
            ) : (
              <div className="space-y-2">
                {conversations.map((conversation) => {
                  const isActive = conversation.id === selectedConversationId;
                  const isRenaming = renamingId === conversation.id;
                  return (
                    <div
                      key={conversation.id}
                      className={cn(
                        "w-full rounded-[24px] border border-transparent bg-white/4 p-4 text-left transition hover:border-white/12 hover:bg-white/7",
                        isActive && "border-primary/40 bg-primary/10",
                      )}
                      data-testid={`conversation-item-${conversation.id}`}
                      onClick={() => void loadConversation(conversation.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          void loadConversation(conversation.id);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          {isRenaming ? (
                            <div
                              className="flex gap-2"
                              onClick={(event) => event.stopPropagation()}
                            >
                              <Input
                                autoFocus
                                value={renameDraft}
                                onChange={(event) => setRenameDraft(event.target.value)}
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") {
                                    event.preventDefault();
                                    void handleRenameConversation(conversation.id);
                                  }
                                }}
                              />
                              <Button
                                size="sm"
                                onClick={() => void handleRenameConversation(conversation.id)}
                              >
                                Save
                              </Button>
                            </div>
                          ) : (
                            <>
                              <div className="flex items-center gap-2">
                                <p className="truncate font-medium text-foreground">
                                  {conversation.title ?? "Untitled conversation"}
                                </p>
                                <Badge>{conversation.status}</Badge>
                              </div>
                              <p className="mt-2 text-xs text-muted-foreground">
                                {new Date(conversation.updated_at).toLocaleString()}
                              </p>
                            </>
                          )}
                        </div>
                        <div
                          className="flex items-center gap-1"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => {
                              setRenamingId(conversation.id);
                              setRenameDraft(conversation.title ?? "");
                            }}
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => void handleDeleteConversation(conversation.id)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader className="border-b border-white/8">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <CardTitle>
                  {detail?.title ?? "Start a conversation"}
                </CardTitle>
                <CardDescription>
                  Same-origin API routes keep the anonymous cookie session stable while the assistant streams.
                </CardDescription>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="space-y-1 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                  Provider
                  <select
                    className="h-10 rounded-2xl border border-input bg-white/5 px-3 text-sm text-foreground"
                    onChange={(event) => setProvider(event.target.value)}
                    value={provider}
                  >
                    {providerOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="space-y-1 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                  Model
                  <select
                    className="h-10 rounded-2xl border border-input bg-white/5 px-3 text-sm text-foreground"
                    onChange={(event) => setModel(event.target.value)}
                    value={model}
                  >
                    {modelOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex min-h-[68vh] flex-col gap-4">
            {loadingDetail ? (
              <div className="space-y-3 pt-4">
                <Skeleton className="h-20 w-4/5" />
                <Skeleton className="ml-auto h-24 w-3/4" />
                <Skeleton className="h-20 w-2/3" />
              </div>
            ) : detail === null ? (
              <div className="flex flex-1 items-center justify-center">
                <div className="max-w-md space-y-4 text-center">
                  <Badge className="bg-white/8">Empty state</Badge>
                  <h2 className="font-display text-2xl font-semibold">
                    No active thread selected
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    Spin up a new conversation to test browser-side SSE parsing, optimistic message rendering, and cancel requests.
                  </p>
                  <Button onClick={() => void handleCreateConversation()}>
                    <Plus className="h-4 w-4" />
                    Start a conversation
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex-1 space-y-4 overflow-y-auto rounded-[24px] border border-white/8 bg-white/3 p-4">
                  {messages.length === 0 ? (
                    <div className="flex h-full min-h-[280px] items-center justify-center">
                      <div className="max-w-md space-y-3 text-center">
                        <Badge className="bg-accent/15 text-accent">Ready to stream</Badge>
                        <p className="text-sm text-muted-foreground">
                          The first message creates a live assistant bubble immediately, then fills it token by token as SSE frames arrive.
                        </p>
                      </div>
                    </div>
                  ) : (
                    messages.map((message) => (
                      <div
                        key={message.id}
                        className={cn(
                          "max-w-[85%] rounded-[24px] border px-4 py-3 shadow-sm",
                          message.role === "user"
                            ? "ml-auto border-primary/30 bg-primary/16 text-primary-foreground"
                            : "border-white/10 bg-card/90",
                        )}
                        data-testid="chat-message"
                      >
                        <div className="mb-2 flex items-center justify-between gap-3">
                          <span className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
                            {message.role}
                          </span>
                          <div className="flex items-center gap-2">
                            <Badge>{message.status}</Badge>
                            {message.streaming ? (
                              <Loader2 className="h-4 w-4 animate-spin text-primary" />
                            ) : null}
                          </div>
                        </div>
                        <p className="whitespace-pre-wrap text-sm leading-7">
                          {message.content || (message.streaming ? "Streaming..." : "No content")}
                        </p>
                      </div>
                    ))
                  )}
                </div>

                <div className="space-y-3 rounded-[24px] border border-white/8 bg-white/4 p-4">
                  <Textarea
                    data-testid="chat-composer"
                    onChange={(event) => setComposer(event.target.value)}
                    placeholder="Ask for a cost summary, a cancellation drill, or a provider comparison."
                    value={composer}
                  />
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm text-muted-foreground">
                      Session cookie auth stays anonymous by default, but your thread list remains isolated to this browser.
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {sending ? (
                        <Button
                          data-testid="stop-stream"
                          onClick={() => void handleStop()}
                          variant="destructive"
                        >
                          <Square className="h-4 w-4" />
                          Stop
                        </Button>
                      ) : null}
                      <Button
                        data-testid="send-message"
                        disabled={composer.trim() === "" || sending}
                        onClick={() => void handleSendMessage()}
                      >
                        {sending ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4" />
                        )}
                        Send
                      </Button>
                    </div>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
