"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Loader2, Send, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAssistantChat } from "@/hooks/api";
import type { AssistantMessage } from "@/lib/api/types";
import { formatApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "dap.assistant.open";

type ChatTurn = AssistantMessage & {
  citations?: { label: string; href: string }[];
};

/**
 * In-app configuration assistant (#689) — a collapsible chat docked on the
 * right. Slice 1 is the UI shell talking to a stub backend; the docs-grounded
 * model reply lands next behind the same endpoint. The panel owns the message
 * history and sends the full transcript each turn.
 */
export function AssistantPanel() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const chat = useAssistantChat();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Restore the open/closed preference once on mount.
  useEffect(() => {
    setOpen(window.localStorage.getItem(STORAGE_KEY) === "1");
  }, []);
  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, open ? "1" : "0");
  }, [open]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, chat.isPending]);

  const send = () => {
    const content = draft.trim();
    if (!content || chat.isPending) return;
    const nextHistory: AssistantMessage[] = [
      ...turns.map((t) => ({ role: t.role, content: t.content })),
      { role: "user", content },
    ];
    setTurns((prev) => [...prev, { role: "user", content }]);
    setDraft("");
    chat.mutate(
      { messages: nextHistory },
      {
        onSuccess: (res) =>
          setTurns((prev) => [
            ...prev,
            { ...res.message, citations: res.citations },
          ]),
      },
    );
  };

  if (!open) {
    return (
      <Button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-4 right-4 z-40 h-11 rounded-full shadow-lg"
        aria-label="Open configuration assistant"
      >
        <Sparkles className="mr-1.5 h-4 w-4" aria-hidden />
        Assistant
      </Button>
    );
  }

  return (
    <aside
      className="fixed right-0 top-0 z-40 flex h-full w-full max-w-md flex-col border-l bg-background shadow-xl"
      aria-label="Configuration assistant"
    >
      <header className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-blue-500" aria-hidden />
          <span className="text-sm font-semibold">Config assistant</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={() => setOpen(false)}
          aria-label="Close assistant"
        >
          <X className="h-4 w-4" />
        </Button>
      </header>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
        {turns.length === 0 && (
          <p className="text-muted-foreground">
            Describe what you want to build — e.g. “an agent that reviews PRs
            cheaply”, “a deterministic, free classifier”, or “a full code
            pipeline like Cortex” — and I’ll suggest a DAP configuration.
          </p>
        )}
        {turns.map((turn, i) => (
          <div
            key={i}
            className={cn(
              "rounded-lg px-3 py-2 max-w-[90%] whitespace-pre-wrap",
              turn.role === "user"
                ? "ml-auto bg-primary text-primary-foreground"
                : "bg-muted",
            )}
          >
            {turn.content}
            {turn.citations && turn.citations.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-2">
                {turn.citations.map((c) => (
                  <Link
                    key={c.href}
                    href={c.href}
                    target="_blank"
                    className="text-xs text-blue-600 hover:underline dark:text-blue-400"
                  >
                    {c.label}
                  </Link>
                ))}
              </div>
            )}
          </div>
        ))}
        {chat.isPending && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Thinking…
          </span>
        )}
        {chat.isError && (
          <p className="text-xs text-destructive" role="alert">
            {formatApiError(chat.error)}
          </p>
        )}
      </div>

      <div className="border-t p-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            placeholder="Ask about agents, pipelines, runtimes, gates…"
            className="min-h-0 resize-none"
            aria-label="Message the assistant"
          />
          <Button
            type="button"
            size="icon"
            onClick={send}
            disabled={!draft.trim() || chat.isPending}
            aria-label="Send"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
        <p className="mt-1.5 text-[10px] text-muted-foreground">
          Suggestions only — never auto-applied. Don’t paste secrets.
        </p>
      </div>
    </aside>
  );
}
