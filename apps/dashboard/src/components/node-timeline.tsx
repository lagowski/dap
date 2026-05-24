"use client";

import { CheckCircle2, Circle, Loader2, XCircle, MinusCircle } from "lucide-react";
import { useRunStateHistory } from "@/hooks/api";
import { formatDuration } from "@/lib/utils";
import type { NodeStatus } from "@/lib/api/types";

const STATUS_ICON: Record<NodeStatus, React.ReactNode> = {
  success: <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />,
  running: <Loader2 className="h-4 w-4 text-blue-500 animate-spin shrink-0" />,
  failed: <XCircle className="h-4 w-4 text-red-500 shrink-0" />,
  skipped: <MinusCircle className="h-4 w-4 text-muted-foreground shrink-0" />,
  pending: <Circle className="h-4 w-4 text-muted-foreground shrink-0" />,
};

const STATUS_BORDER: Record<NodeStatus, string> = {
  success: "border-green-300 dark:border-green-700",
  running: "border-blue-300 dark:border-blue-700",
  failed: "border-red-300 dark:border-red-700",
  skipped: "border-muted",
  pending: "border-muted",
};

export function NodeTimeline({
  nodeStatuses,
  currentNode,
  runId,
}: {
  nodeStatuses: Record<string, NodeStatus>;
  currentNode: string | null;
  runId: string;
}) {
  const { data: history } = useRunStateHistory(runId);
  const entries = Object.entries(nodeStatuses);

  if (entries.length === 0) return null;

  // Derive per-node elapsed time from state history snapshots.
  // Each snapshot marks when a node was entered; duration is the gap
  // between consecutive snapshots (or now for the currently running node).
  const elapsed = new Map<string, number>();
  if (history && history.length > 0) {
    const sorted = [...history].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
    );
    for (let i = 0; i < sorted.length; i++) {
      const snap = sorted[i];
      const start = new Date(snap.timestamp).getTime();
      const end =
        i + 1 < sorted.length
          ? new Date(sorted[i + 1].timestamp).getTime()
          : Date.now();
      const status = nodeStatuses[snap.node_id];
      if (status === "success" || status === "failed" || status === "running") {
        elapsed.set(snap.node_id, end - start);
      }
    }
  }

  return (
    <ol className="space-y-0" role="list" aria-label="Node timeline">
      {entries.map(([nodeId, status], idx) => (
        <li key={nodeId} className="flex items-stretch gap-3">
          <div className="flex flex-col items-center">
            <div className="py-1">{STATUS_ICON[status]}</div>
            {idx < entries.length - 1 && (
              <div
                className={`flex-1 w-px border-l-2 ${STATUS_BORDER[status]}`}
              />
            )}
          </div>
          <div className="flex items-center gap-3 pb-3 pt-1 min-w-0">
            <span
              className={`font-mono text-sm ${
                nodeId === currentNode ? "font-semibold text-foreground" : "text-muted-foreground"
              }`}
            >
              {nodeId}
            </span>
            {elapsed.has(nodeId) && (
              <span className="text-xs text-muted-foreground tabular-nums" suppressHydrationWarning>
                {formatDuration(elapsed.get(nodeId)!)}
              </span>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
