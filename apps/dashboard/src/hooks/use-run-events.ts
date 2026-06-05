"use client";

/**
 * Live run-events hook (#662 Phase 3c).
 *
 * Subscribes to the engine's Server-Sent Events stream for a run and
 * surfaces the streamed ``node_log`` stdout fragments as an accumulated,
 * capped buffer for the live-output panel.
 *
 * Transport: the browser opens ``new EventSource("/api/runs/<id>/events")``
 * against the dashboard's own origin. The catch-all BFF proxy
 * (``app/api/[...path]/route.ts``) reads the httpOnly JWT cookie, forwards
 * it as ``Authorization: Bearer``, and streams the engine response body
 * through unbuffered — so EventSource authenticates and streams with no
 * dedicated route.
 *
 * Engine event contract (flat JSON ``data`` per frame):
 *   - ``snapshot``      {run_id, final_status, current_node, node_statuses, ended_at}
 *   - ``run_status``    {run_id, final_status, current_node}
 *   - ``node_started``  {run_id, node_id}
 *   - ``node_finished`` {run_id, node_id, status, ...}
 *   - ``node_log``      {run_id, node_id, seq, content, stream}  ← live stdout
 *   - ``run_finished``  {run_id, final_status, ended_at}  (stream then closes)
 *
 * The engine only emits ``node_log`` for chunks produced *after* connect
 * (no history replay), and sends ``snapshot`` once on connect — so the
 * browser's built-in EventSource auto-reconnect on a transient drop won't
 * duplicate history.
 *
 * SSR-safe: ``EventSource`` is browser-only, so the connection is opened
 * only inside an effect after guarding ``typeof window``.
 */

import { useEffect, useRef, useState } from "react";

/**
 * Max number of streamed log lines retained in memory. The panel tails a
 * long-running node (e.g. a 16-minute cortex agent) which can emit a lot
 * of output; we keep the most recent ``RUN_LOG_LINE_CAP`` lines and drop
 * the oldest so memory stays bounded.
 */
export const RUN_LOG_LINE_CAP = 1000;

/** One streamed stdout/stderr fragment from a node. */
export interface NodeLogLine {
  node_id: string;
  seq: number;
  content: string;
  stream: "stdout" | "stderr";
}

export interface UseRunEventsResult {
  /** Accumulated, capped buffer of streamed ``node_log`` fragments. */
  logLines: NodeLogLine[];
  /** True while the SSE stream is open and the run hasn't finished. */
  isStreaming: boolean;
  /** Current node id surfaced from ``snapshot`` / ``run_status``, if known. */
  currentNode: string | null;
}

interface NodeLogData {
  node_id?: unknown;
  seq?: unknown;
  content?: unknown;
  stream?: unknown;
}

interface RunStatusData {
  current_node?: unknown;
}

/** Defensive JSON parse — returns ``null`` on malformed frames. */
function parseFrame<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function useRunEvents(
  runId: string,
  { enabled }: { enabled: boolean },
): UseRunEventsResult {
  const [logLines, setLogLines] = useState<NodeLogLine[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentNode, setCurrentNode] = useState<string | null>(null);

  // Keep a ref to the live source so the run_finished handler can close it
  // without re-subscribing the effect.
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!enabled) return;
    // EventSource is browser-only; bail out under SSR / non-DOM runtimes.
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      return;
    }

    // Fresh subscription: reset the buffer so a re-enable / run change
    // doesn't show another run's tail. (snapshot-on-connect means the
    // engine re-sends current state.)
    setLogLines([]);
    setCurrentNode(null);
    setIsStreaming(true);

    const source = new EventSource(`/api/runs/${runId}/events`);
    sourceRef.current = source;

    const onSnapshotOrStatus = (ev: MessageEvent) => {
      const data = parseFrame<RunStatusData>(ev.data);
      if (data && typeof data.current_node === "string") {
        setCurrentNode(data.current_node);
      } else if (data && data.current_node === null) {
        setCurrentNode(null);
      }
    };

    const onNodeLog = (ev: MessageEvent) => {
      const data = parseFrame<NodeLogData>(ev.data);
      if (
        !data ||
        typeof data.node_id !== "string" ||
        typeof data.content !== "string"
      ) {
        return;
      }
      const line: NodeLogLine = {
        node_id: data.node_id,
        seq: typeof data.seq === "number" ? data.seq : 0,
        content: data.content,
        stream: data.stream === "stderr" ? "stderr" : "stdout",
      };
      setLogLines((prev) => {
        const next = prev.length >= RUN_LOG_LINE_CAP ? prev.slice(prev.length - RUN_LOG_LINE_CAP + 1) : prev.slice();
        next.push(line);
        return next;
      });
    };

    const onRunFinished = () => {
      setIsStreaming(false);
      source.close();
      sourceRef.current = null;
    };

    source.addEventListener("snapshot", onSnapshotOrStatus);
    source.addEventListener("run_status", onSnapshotOrStatus);
    source.addEventListener("node_log", onNodeLog);
    source.addEventListener("run_finished", onRunFinished);

    return () => {
      source.removeEventListener("snapshot", onSnapshotOrStatus);
      source.removeEventListener("run_status", onSnapshotOrStatus);
      source.removeEventListener("node_log", onNodeLog);
      source.removeEventListener("run_finished", onRunFinished);
      source.close();
      sourceRef.current = null;
      setIsStreaming(false);
    };
  }, [runId, enabled]);

  return { logLines, isStreaming, currentNode };
}
