"use client";

import {
  startTransition,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertCircle,
  ArrowUp,
  BarChart3,
  Check,
  ChevronDown,
  Code2,
  Copy,
  Edit3,
  GraduationCap,
  Lightbulb,
  Loader2,
  MessageSquarePlus,
  PenLine,
  PanelLeft,
  Square,
  Trash2,
  Zap,
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
import { Button } from "@/components/ui/button";
import { MessageContent } from "@/components/chat/message-content";
import { ThemeToggle } from "@/components/theme-toggle";

const providerOptions = [
  { value: "huggingface", label: "HuggingFace" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "grok", label: "Grok" },
] as const;

const modelOptionsByProvider: Record<string, string[]> = {
  huggingface: ["Qwen/Qwen2.5-72B-Instruct", "meta-llama/Llama-3.1-70B-Instruct"],
  openai: ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"],
  anthropic: ["claude-sonnet-4-20250514", "claude-3-5-sonnet", "claude-3-5-haiku"],
  gemini: ["gemini-2.5-flash", "gemini-1.5-pro"],
  deepseek: ["deepseek-chat"],
  grok: ["grok-3-mini", "grok-2"],
};

// Claude-style starter pills, adapted to OlliveLogs context.
const starterPills = [
  { icon: Code2, label: "Code", prompt: "Help me write a Python async batch consumer for Redis Streams." },
  { icon: PenLine, label: "Write", prompt: "Draft a release note for 'PII redaction now runs in the worker'." },
  { icon: GraduationCap, label: "Learn", prompt: "Explain Redis Streams vs Kafka in one paragraph." },
  { icon: Zap, label: "Brainstorm", prompt: "Give me 5 ideas to cut LLM costs without losing quality." },
  { icon: Lightbulb, label: "OlliveLogs", prompt: "What does this project do? Summarize OlliveLogs for a hiring manager." },
] as const;

function timeOfDayGreeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Up late";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  if (h < 21) return "Good evening";
  return "Up late";
}

function deriveConversationTitle(text: string, maxLength = 48): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized === "") return "New chat";
  if (normalized.length <= maxLength) return normalized;
  const clipped = normalized.slice(0, maxLength + 1);
  const boundary = clipped.lastIndexOf(" ");
  const base = (boundary > 0 ? clipped.slice(0, boundary) : normalized.slice(0, maxLength))
    .replace(/[ ,.;:-]+$/g, "");
  return `${base}...`;
}

export function ChatWorkspace() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [composer, setComposer] = useState("");
  const [provider, setProvider] = useState<string>("huggingface");
  const [model, setModel] = useState<string>("Qwen/Qwen2.5-72B-Instruct");
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [loadingSidebar, setLoadingSidebar] = useState(true);
  const [, setLoadingDetail] = useState(false);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [sending, setSending] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    void refreshConversations();
  }, []);

  const messages = useMemo<UiMessage[]>(() => detail?.messages ?? [], [detail]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    const ta = composerRef.current;
    if (ta === null) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 220)}px`;
  }, [composer]);

  const availableModels = modelOptionsByProvider[provider] ?? [];
  const activeTitle =
    detail?.title?.trim() ||
    (detail?.messages.find((message) => message.role === "user")?.content
      ? deriveConversationTitle(
          detail.messages.find((message) => message.role === "user")?.content ?? "",
        )
      : detail !== null
      ? "Untitled"
      : "New chat");

  // ─── Handlers (preserve Codex's contract) ───
  async function refreshConversations(preferredId?: string | null): Promise<void> {
    setLoadingSidebar(true);
    setGlobalError(null);
    try {
      const response = await listConversations();
      setConversations(response.items);
      const nextSelectedId =
        preferredId ?? selectedConversationId ?? null;
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
      if (nextDetail.model_default !== null) {
        setModel(nextDetail.model_default);
      }
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Failed to open conversation.");
    } finally {
      setLoadingDetail(false);
    }
  }

  async function handleNewChat(): Promise<void> {
    setCreatingConversation(true);
    setGlobalError(null);
    try {
      // Clear selection — empty state will show centered greeting.
      setSelectedConversationId(null);
      setDetail(null);
      setComposer("");
      composerRef.current?.focus();
    } finally {
      setCreatingConversation(false);
    }
  }

  async function handleRename(conversationId: string): Promise<void> {
    if (renameDraft.trim() === "") {
      setRenamingId(null);
      return;
    }
    try {
      const renamed = await renameConversation(conversationId, renameDraft.trim());
      setConversations((c) => c.map((x) => (x.id === conversationId ? renamed : x)));
      setDetail((d) => (d?.id === conversationId ? { ...d, title: renamed.title } : d));
      setRenamingId(null);
      setRenameDraft("");
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Rename failed.");
    }
  }

  async function handleDelete(conversationId: string): Promise<void> {
    try {
      await deleteConversation(conversationId);
      const remaining = conversations.filter((c) => c.id !== conversationId);
      setConversations(remaining);
      if (selectedConversationId === conversationId) {
        setSelectedConversationId(null);
        setDetail(null);
      }
    } catch (error: unknown) {
      setGlobalError(error instanceof Error ? error.message : "Delete failed.");
    }
  }

  async function handleSend(): Promise<void> {
    const trimmed = composer.trim();
    if (trimmed === "" || sending) return;
    setGlobalError(null);
    setSending(true);

    let conversationId = selectedConversationId;
    if (conversationId === null) {
      const created = await createConversation({ model_default: model });
      setConversations((c) => [created, ...c]);
      conversationId = created.id;
      setSelectedConversationId(created.id);
      setDetail({ ...created, messages: [] });
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
        : { ...current, messages: [...current.messages, optimisticUser, optimisticAssistant] },
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
                    messages: current.messages.map((m) =>
                      m.id === optimisticAssistant.id
                        ? { ...m, content: `${m.content}${event.delta}` }
                        : m,
                    ),
                  },
            );
          },
          onDone: (event) => commitFinal(optimisticAssistant.id, event),
          onCancelled: (event) => commitCancelled(optimisticAssistant.id, event),
          onError: (event) => commitError(optimisticAssistant.id, event),
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
              messages: current.messages.map((m) =>
                m.id === optimisticAssistant.id ? { ...m, status: "error", streaming: false } : m,
              ),
            },
      );
    } finally {
      setSending(false);
    }
  }

  async function handleStop(): Promise<void> {
    if (selectedConversationId === null) return;
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
      setDetail((d) =>
        d?.id === preferredId
          ? { ...d, title: selected.title, status: selected.status, updated_at: selected.updated_at }
          : d,
      );
    }
  }

  function commitFinal(optId: string, event: SseDoneEvent) {
    setDetail((d) =>
      d === null
        ? null
        : { ...d, messages: d.messages.map((m) => (m.id === optId ? event.message : m)) },
    );
  }
  function commitCancelled(optId: string, event: SseCancelledEvent) {
    setDetail((d) =>
      d === null
        ? null
        : {
            ...d,
            status: "cancelled",
            messages: d.messages.map((m) => (m.id === optId ? event.message : m)),
          },
    );
  }
  function commitError(optId: string, event: { message: MessageResource; reason: string }) {
    setGlobalError(event.reason);
    setDetail((d) =>
      d === null
        ? null
        : { ...d, messages: d.messages.map((m) => (m.id === optId ? event.message : m)) },
    );
  }

  function touchConversation(conversationId: string, fallbackTitle: string) {
    startTransition(() => {
      setConversations((current) => {
        const updated = current.map((c) =>
          c.id === conversationId
            ? {
                ...c,
                title: c.title ?? deriveConversationTitle(fallbackTitle),
                updated_at: new Date().toISOString(),
              }
            : c,
        );
        return [...updated].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      });
    });
  }

  function onComposerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void handleSend();
    }
  }

  // ─── Render ───
  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      {/* Sidebar */}
      <aside
        className={cn(
          "flex h-full shrink-0 flex-col border-r-2 border-foreground bg-sidebar text-sidebar-foreground transition-[width] duration-200",
          sidebarOpen ? "w-[270px]" : "w-0 overflow-hidden",
        )}
      >
        <div className="flex items-center justify-between px-4 pb-3 pt-4">
          <span className="font-display text-2xl font-bold tracking-tight">
            OlliveLogs
          </span>
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            className="rounded-md p-1.5 text-foreground/70 hover:bg-muted hover:text-foreground"
            aria-label="Collapse sidebar"
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        </div>

        <div className="px-3 pb-3">
          <Button
            variant="primary"
            size="default"
            onClick={() => void handleNewChat()}
            disabled={creatingConversation}
            className="w-full"
            data-testid="new-chat"
          >
            {creatingConversation ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <MessageSquarePlus className="h-4 w-4" />
            )}
            New chat
          </Button>
        </div>

        <div className="px-4 pb-1 pt-3 text-[11px] font-bold uppercase tracking-widest text-muted-foreground">
          Recents
        </div>

        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {loadingSidebar ? (
            <div className="space-y-1.5 px-1">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-9 animate-pulse rounded-md border-2 border-foreground/20 bg-muted/60" />
              ))}
            </div>
          ) : conversations.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              No conversations yet.
              <br />
              Click "New chat" above to start.
            </p>
          ) : (
            <ul className="space-y-1">
              {conversations.map((c) => {
                const isActive = c.id === selectedConversationId;
                const isRenaming = renamingId === c.id;
                return (
                  <li key={c.id}>
                    {isRenaming ? (
                      <input
                        autoFocus
                        aria-label="Rename conversation"
                        placeholder="Rename…"
                        value={renameDraft}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onBlur={() => void handleRename(c.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void handleRename(c.id);
                          if (e.key === "Escape") {
                            setRenamingId(null);
                            setRenameDraft("");
                          }
                        }}
                        className="w-full rounded-md border-2 border-foreground bg-input px-2.5 py-1.5 text-sm outline-none shadow-brutal-sm"
                      />
                    ) : (
                      <button
                        type="button"
                        onClick={() => void loadConversation(c.id)}
                        data-testid={`conversation-item-${c.id}`}
                        className={cn(
                          "group flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm font-medium transition-all duration-100",
                          isActive
                            ? "border-2 border-foreground bg-surface shadow-brutal-sm"
                            : "border-2 border-transparent text-foreground/80 hover:border-foreground hover:bg-surface hover:text-foreground hover:shadow-brutal-sm",
                        )}
                      >
                        <span className="flex-1 truncate">
                          {c.title?.trim() || "Untitled"}
                        </span>
                        <span className="hidden gap-0.5 group-hover:flex">
                          <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => {
                              e.stopPropagation();
                              setRenamingId(c.id);
                              setRenameDraft(c.title ?? "");
                            }}
                            className="rounded p-1 hover:bg-background"
                            aria-label="Rename"
                          >
                            <Edit3 className="h-3 w-3" />
                          </span>
                          <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleDelete(c.id);
                            }}
                            className="rounded p-1 hover:bg-background hover:text-destructive"
                            aria-label="Delete"
                          >
                            <Trash2 className="h-3 w-3" />
                          </span>
                        </span>
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t-2 border-foreground bg-surface px-3 py-2.5">
          <a
            href="/dashboard"
            className="flex items-center gap-1.5 rounded-md border-2 border-transparent px-2 py-1 text-xs font-semibold text-foreground/80 transition-all duration-100 hover:border-foreground hover:bg-background hover:text-foreground hover:shadow-brutal-sm"
          >
            <BarChart3 className="h-3.5 w-3.5" />
            Dashboard
          </a>
          <ThemeToggle />
        </div>
      </aside>

      {/* Main column */}
      <main className="flex h-full min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="flex h-14 shrink-0 items-center justify-between border-b-2 border-foreground bg-background px-4">
          <div className="flex min-w-0 items-center gap-2">
            {!sidebarOpen ? (
              <button
                type="button"
                onClick={() => setSidebarOpen(true)}
                className="rounded-md border-2 border-foreground bg-surface p-1.5 shadow-brutal-sm hover:-translate-y-px hover:shadow-brutal"
                aria-label="Open sidebar"
              >
                <PanelLeft className="h-4 w-4" />
              </button>
            ) : null}
            <h1 className="truncate text-sm font-semibold">{activeTitle}</h1>
            {detail?.status === "cancelled" ? (
              <span className="ml-2 rounded-md border-2 border-foreground bg-accent px-2 py-0.5 text-xs font-bold text-accent-foreground">
                cancelled
              </span>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            <ProviderPicker
              value={provider}
              onChange={(v) => {
                setProvider(v);
                const first = modelOptionsByProvider[v]?.[0];
                if (first !== undefined) setModel(first);
              }}
            />
            <ModelPicker models={availableModels} value={model} onChange={setModel} />
          </div>
        </header>

        {/* Messages or empty state */}
        <div className="relative flex-1 overflow-y-auto">
          {globalError !== null ? (
            <div className="mx-auto mt-4 flex max-w-3xl items-start gap-3 rounded-brutal border-2 border-destructive bg-destructive/15 px-4 py-3 text-sm text-destructive shadow-brutal-sm">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="flex-1 font-medium">{globalError}</div>
              <button
                type="button"
                onClick={() => setGlobalError(null)}
                className="text-xs font-semibold text-destructive/80 hover:text-destructive"
              >
                dismiss
              </button>
            </div>
          ) : null}

          {messages.length === 0 ? (
            <EmptyState
              greeting={timeOfDayGreeting()}
              onPromptClick={(p) => {
                setComposer(p);
                composerRef.current?.focus();
              }}
              composer={composer}
              setComposer={setComposer}
              onComposerKeyDown={onComposerKeyDown}
              sending={sending}
              handleSend={handleSend}
              handleStop={handleStop}
              composerRef={composerRef}
              provider={provider}
              model={model}
            />
          ) : (
            <>
              <div className="mx-auto w-full max-w-3xl px-4 py-6">
                {messages.map((m, idx) => (
                  <MessageRow key={m.id} message={m} isLast={idx === messages.length - 1} />
                ))}
                <div ref={messagesEndRef} />
              </div>
            </>
          )}
        </div>

        {/* Composer (only shown when conversation has messages — otherwise it's centered in empty state) */}
        {messages.length > 0 ? (
          <div className="border-t-2 border-foreground bg-background px-4 pb-4 pt-3">
            <div className="mx-auto w-full max-w-3xl">
              <Composer
                composerRef={composerRef}
                composer={composer}
                setComposer={setComposer}
                onComposerKeyDown={onComposerKeyDown}
                sending={sending}
                handleSend={handleSend}
                handleStop={handleStop}
              />
              <p className="mt-2 text-center text-xs text-muted-foreground">
                {sending
                  ? "Streaming response… press the stop button to cancel"
                  : "Press Enter to send, Shift+Enter for a newline"}
              </p>
            </div>
          </div>
        ) : null}
      </main>
    </div>
  );
}

// ─── Sub-components ───

function Composer({
  composerRef,
  composer,
  setComposer,
  onComposerKeyDown,
  sending,
  handleSend,
  handleStop,
}: {
  composerRef: React.RefObject<HTMLTextAreaElement>;
  composer: string;
  setComposer: (v: string) => void;
  onComposerKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  sending: boolean;
  handleSend: () => Promise<void>;
  handleStop: () => Promise<void>;
}) {
  return (
    <div className="flex items-end gap-2 rounded-brutal-lg border-2 border-foreground bg-surface px-4 py-3 shadow-brutal focus-within:translate-y-0.5 focus-within:shadow-brutal-sm">
      <textarea
        ref={composerRef}
        value={composer}
        onChange={(e) => setComposer(e.target.value)}
        onKeyDown={onComposerKeyDown}
        placeholder="How can I help you today?"
        rows={1}
        disabled={sending}
        className="flex-1 resize-none bg-transparent py-1 text-[15px] leading-6 text-foreground outline-none placeholder:text-foreground/50 disabled:opacity-50"
        data-testid="composer-input"
      />
      {sending ? (
        <button
          type="button"
          onClick={() => void handleStop()}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border-2 border-foreground bg-accent text-accent-foreground shadow-brutal-sm transition-all duration-100 hover:-translate-y-px hover:shadow-brutal active:translate-y-1 active:shadow-brutal-pressed"
          aria-label="Stop"
          data-testid="stop-button"
        >
          <Square className="h-3.5 w-3.5 fill-current" />
        </button>
      ) : (
        <button
          type="button"
          onClick={() => void handleSend()}
          disabled={composer.trim() === ""}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border-2 border-foreground bg-primary text-primary-foreground shadow-brutal-sm transition-all duration-100 hover:-translate-y-px hover:shadow-brutal active:translate-y-1 active:shadow-brutal-pressed disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground disabled:shadow-brutal-pressed"
          aria-label="Send"
          data-testid="send-button"
        >
          <ArrowUp className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

function ProviderPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <details className="relative">
      <summary className="flex cursor-pointer list-none items-center gap-1 rounded-md border-2 border-foreground bg-surface px-2.5 py-1 text-xs font-bold text-foreground shadow-brutal-sm transition-all duration-100 hover:-translate-y-px hover:shadow-brutal [&::-webkit-details-marker]:hidden">
        {providerOptions.find((p) => p.value === value)?.label ?? value}
        <ChevronDown className="h-3 w-3" />
      </summary>
      <ul className="absolute right-0 z-20 mt-1 w-44 overflow-hidden rounded-md border-2 border-foreground bg-surface shadow-brutal">
        {providerOptions.map((p) => (
          <li key={p.value} className="border-b border-foreground/20 last:border-b-0">
            <button
              type="button"
              onClick={(e) => {
                onChange(p.value);
                (e.currentTarget.closest("details") as HTMLDetailsElement | null)?.removeAttribute("open");
              }}
              className={cn(
                "block w-full px-3 py-1.5 text-left text-xs font-semibold hover:bg-accent hover:text-accent-foreground",
                value === p.value && "bg-accent/40",
              )}
            >
              {p.label}
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}

function ModelPicker({
  models,
  value,
  onChange,
}: {
  models: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  if (models.length === 0) {
    return (
      <span className="rounded-md border-2 border-foreground bg-surface px-2.5 py-1 font-mono text-xs text-muted-foreground shadow-brutal-sm">
        {value}
      </span>
    );
  }
  return (
    <details className="relative">
      <summary className="flex max-w-[240px] cursor-pointer list-none items-center gap-1 rounded-md border-2 border-foreground bg-surface px-2.5 py-1 font-mono text-xs text-foreground shadow-brutal-sm transition-all duration-100 hover:-translate-y-px hover:shadow-brutal [&::-webkit-details-marker]:hidden">
        <span className="truncate">{value}</span>
        <ChevronDown className="h-3 w-3 shrink-0" />
      </summary>
      <ul className="absolute right-0 z-20 mt-1 w-72 overflow-hidden rounded-md border-2 border-foreground bg-surface shadow-brutal">
        {models.map((m) => (
          <li key={m} className="border-b border-foreground/20 last:border-b-0">
            <button
              type="button"
              onClick={(e) => {
                onChange(m);
                (e.currentTarget.closest("details") as HTMLDetailsElement | null)?.removeAttribute("open");
              }}
              className={cn(
                "block w-full px-3 py-1.5 text-left font-mono text-xs hover:bg-accent hover:text-accent-foreground",
                value === m && "bg-accent/40 font-bold",
              )}
            >
              {m}
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}

function MessageRow({ message, isLast }: { message: UiMessage; isLast: boolean }) {
  const isUser = message.role === "user";
  const isCancelled = message.status === "cancelled";
  const isError = message.status === "error";
  const [copied, setCopied] = useState(false);

  async function handleCopy(): Promise<void> {
    if (message.content.trim() === "") return;
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div
      className={cn(
        "msg-enter group mb-6 flex gap-3",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      {!isUser ? (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border-2 border-foreground bg-primary text-[11px] font-bold text-primary-foreground shadow-brutal-sm">
          OL
        </div>
      ) : null}

      <div className={cn("min-w-0 max-w-[85%]", isUser && "items-end")}>
        {isUser ? (
          <div className="rounded-brutal border-2 border-foreground bg-userBubble px-4 py-2.5 text-[15px] leading-6 text-foreground shadow-brutal-sm">
            {message.content}
          </div>
        ) : message.content ? (
          <MessageContent
            content={message.content}
            className={cn(
              message.streaming && isLast && "streaming-cursor",
              isCancelled && "italic text-muted-foreground",
              isError && "text-destructive",
            )}
          />
        ) : (
          <div
            className={cn(
              "whitespace-pre-wrap text-[15px] leading-7 text-foreground",
              message.streaming && isLast && "streaming-cursor",
              isCancelled && "italic text-muted-foreground",
              isError && "text-destructive",
            )}
          >
            {message.streaming
              ? ""
              : isCancelled
              ? "(generation cancelled)"
              : isError
              ? "(generation failed)"
              : ""}
          </div>
        )}
        {!isUser && (isCancelled || isError) ? (
          <p className="mt-1.5 text-xs font-medium text-muted-foreground">
            {isCancelled ? "Stopped by user" : "Provider returned an error"}
          </p>
        ) : null}
        {!isUser && message.content.trim() !== "" ? (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => void handleCopy()}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border-2 border-foreground bg-surface px-2.5 py-1 text-[11px] font-bold text-foreground/80 shadow-brutal-sm transition-all duration-150",
                "hover:-translate-y-px hover:bg-primary hover:text-primary-foreground hover:shadow-brutal hover:ring-2 hover:ring-primary/30",
                copied && "bg-primary text-primary-foreground",
              )}
              aria-label="Copy message"
            >
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        ) : null}
      </div>

      {isUser ? (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border-2 border-foreground bg-accent text-[11px] font-bold text-accent-foreground shadow-brutal-sm">
          YOU
        </div>
      ) : null}
    </div>
  );
}

function EmptyState({
  greeting,
  onPromptClick,
  composer,
  setComposer,
  onComposerKeyDown,
  sending,
  handleSend,
  handleStop,
  composerRef,
  provider,
  model,
}: {
  greeting: string;
  onPromptClick: (p: string) => void;
  composer: string;
  setComposer: (v: string) => void;
  onComposerKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  sending: boolean;
  handleSend: () => Promise<void>;
  handleStop: () => Promise<void>;
  composerRef: React.RefObject<HTMLTextAreaElement>;
  provider: string;
  model: string;
}) {
  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center px-6 py-10">
      {/* Logo mark */}
      <div className="float-tilt mb-6 flex h-16 w-16 items-center justify-center rounded-brutal border-2 border-foreground bg-primary text-2xl font-black text-primary-foreground shadow-brutal-lg">
        OL
      </div>

      {/* Greeting */}
      <h2 className="font-display text-center text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
        {greeting},{" "}
        <span className="bg-gradient-to-r from-primary via-accent to-primary bg-clip-text text-transparent">
          Abhishek
        </span>
      </h2>
      <p className="mt-3 text-center text-sm font-medium text-muted-foreground">
        {provider} · <span className="font-mono">{model}</span>
      </p>

      {/* Composer (centered, like Claude's start state) */}
      <div className="mt-8 w-full">
        <Composer
          composerRef={composerRef}
          composer={composer}
          setComposer={setComposer}
          onComposerKeyDown={onComposerKeyDown}
          sending={sending}
          handleSend={handleSend}
          handleStop={handleStop}
        />
      </div>

      {/* Starter pills */}
      <div className="mt-5 flex flex-wrap justify-center gap-2">
        {starterPills.map(({ icon: Icon, label, prompt }) => (
          <button
            key={label}
            type="button"
            onClick={() => onPromptClick(prompt)}
            className="flex items-center gap-1.5 rounded-brutal border-2 border-foreground bg-surface px-3 py-1.5 text-xs font-bold text-foreground shadow-brutal-sm transition-all duration-100 hover:-translate-y-px hover:shadow-brutal active:translate-y-1 active:shadow-brutal-pressed"
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      <p className="mt-6 text-center text-xs text-muted-foreground">
        Streaming multi-provider chat · every call logged to ClickHouse
      </p>
    </div>
  );
}
