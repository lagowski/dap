"use client";

/**
 * Live-output panel (#662 Phase 3c).
 *
 * A collapsible, auto-scrolling ("tail -f") view of a running node's
 * streamed stdout. Subscribes to the engine SSE stream via
 * ``useRunEvents`` and renders the accumulated ``node_log`` fragments as
 * monospace text. The panel is the visible payoff of #662: an operator
 * watching a long node (e.g. a 16-minute cortex agent) sees its output as
 * it happens instead of an opaque spinner.
 *
 * Auto-scroll follows the newest line unless the user has scrolled up to
 * read back-history (standard terminal-tail behaviour) — scrolling back to
 * the bottom re-engages follow.
 *
 * The parent gates ``enabled`` (Live toggle on AND run running/paused), so
 * this component never opens an EventSource for terminal runs or when Live
 * is off.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Terminal } from "lucide-react";

import { useRunEvents } from "@/hooks/use-run-events";
import { Card } from "@/components/ui/card";

interface LiveOutputPanelProps {
  runId: string;
  /** Live toggle on AND run is running/paused. Drives the SSE subscription. */
  enabled: boolean;
}

export function LiveOutputPanel({ runId, enabled }: LiveOutputPanelProps) {
  const { logLines, isStreaming, currentNode } = useRunEvents(runId, {
    enabled,
  });
  const [collapsed, setCollapsed] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  // ``follow`` tracks whether we auto-scroll to the bottom on new output.
  // It disengages when the user scrolls up and re-engages at the bottom.
  const followRef = useRef(true);

  const text = useMemo(
    () => logLines.map((l) => l.content).join(""),
    [logLines],
  );

  // Auto-scroll to the bottom when new output arrives, unless the user has
  // scrolled up. Runs after each render that changes the text.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || collapsed || !followRef.current) return;
    el.scrollTo({ top: el.scrollHeight });
  }, [text, collapsed]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    // Within ~16px of the bottom counts as "at bottom" → keep following.
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 16;
    followRef.current = atBottom;
  };

  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-sm font-medium hover:bg-accent/50"
      >
        {collapsed ? (
          <ChevronRight className="h-4 w-4 shrink-0" />
        ) : (
          <ChevronDown className="h-4 w-4 shrink-0" />
        )}
        <Terminal className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span>Live output</span>
        {currentNode && (
          <span className="font-mono text-xs text-muted-foreground">
            {currentNode}
          </span>
        )}
        {isStreaming && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            streaming
          </span>
        )}
      </button>

      {!collapsed && (
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="max-h-80 overflow-auto border-t bg-muted/40 px-4 py-3 font-mono text-xs leading-relaxed"
        >
          {text ? (
            <pre className="whitespace-pre-wrap break-words">{text}</pre>
          ) : isStreaming ? (
            <p className="text-muted-foreground">
              Waiting for output… (non-CLI nodes don&apos;t stream stdout)
            </p>
          ) : (
            <p className="text-muted-foreground">No streamed output.</p>
          )}
        </div>
      )}
    </Card>
  );
}
