"use client";

import { CheckCircle2, Circle, Loader2, XCircle, MinusCircle } from "lucide-react";
import { useAgentsList, useRunNodeLogs } from "@/hooks/api";
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
  onSelectNode,
  selectedNode,
}: {
  nodeStatuses: Record<string, NodeStatus>;
  currentNode: string | null;
  runId: string;
  onSelectNode?: (nodeId: string) => void;
  selectedNode?: string | null;
}) {
  const { data: nodeLogs } = useRunNodeLogs(runId);
  // Each node runs an agent (#707). The execution log already carries
  // ``agent_id`` + ``runtime_id``; resolve the human-readable agent name from
  // the (cached, shared) agents list rather than fetching per row. The name
  // reflects the agent's *current* name — if an agent was renamed after the
  // run, the list shows the new name; that's an accepted trade-off for not
  // persisting agent_name on every log row.
  const { data: agentsList } = useAgentsList();
  const entries = Object.entries(nodeStatuses);

  if (entries.length === 0) return null;

  const agentNameById = new Map<string, string>();
  for (const agent of agentsList?.items ?? []) {
    agentNameById.set(agent.id, agent.name);
  }

  // Build a per-node timing map from execution logs.
  // Execution logs have accurate started_at / ended_at / duration_ms written
  // atomically per node, so they are immune to the DB-drop bug that caused
  // state-history snapshots to produce inflated durations (#609):
  //
  //   Old approach (state history): last snapshot used Date.now() as end time.
  //   When pr_creator/pr_merger snapshots were missing (DB hiccup), finalize
  //   appeared last → its elapsed grew unbounded → showed "20m 0s".
  //
  //   New approach (execution logs): duration_ms is committed together with the
  //   log row in the same transaction. If the row exists, the value is real.
  //   If the row is missing (DB dropped before commit), we show no timing for
  //   that node rather than an inflated one.
  const logByNode = new Map<
    string,
    {
      started_at: string;
      ended_at: string | null;
      duration_ms: number;
      agent_id: string;
      runtime_id: string;
    }
  >();
  if (nodeLogs) {
    for (const log of nodeLogs) {
      logByNode.set(log.node_id, {
        started_at: log.started_at,
        ended_at: log.ended_at ?? null,
        duration_ms: log.duration_ms,
        agent_id: log.agent_id,
        runtime_id: log.runtime_id,
      });
    }
  }

  const getAgentLabel = (nodeId: string): { name: string; runtime: string } | null => {
    const log = logByNode.get(nodeId);
    if (!log || !log.agent_id) return null;
    // Name from the agents list, else a short prefix of the agent id.
    const name = agentNameById.get(log.agent_id) ?? log.agent_id.slice(0, 8);
    return { name, runtime: log.runtime_id };
  };

  const getElapsed = (nodeId: string, status: NodeStatus): number | null => {
    const log = logByNode.get(nodeId);
    if (!log) return null;
    // Prefer pre-computed duration_ms (always accurate).
    if (log.duration_ms > 0) return log.duration_ms;
    // Fallback: compute from timestamps (covers edge case where duration_ms=0
    // but ended_at is set, e.g. very fast nodes rounded to 0 ms).
    if (log.ended_at) {
      return new Date(log.ended_at).getTime() - new Date(log.started_at).getTime();
    }
    // Node is still running — show live elapsed from started_at.
    if (status === "running") {
      return Date.now() - new Date(log.started_at).getTime();
    }
    return null;
  };

  return (
    <ol className="space-y-0" role="list" aria-label="Node timeline">
      {entries.map(([nodeId, status], idx) => {
        const elapsed = getElapsed(nodeId, status);
        const agent = getAgentLabel(nodeId);
        return (
          <li key={nodeId} className="flex items-stretch gap-3">
            <div className="flex flex-col items-center">
              <div className="py-1">{STATUS_ICON[status]}</div>
              {idx < entries.length - 1 && (
                <div
                  className={`flex-1 w-px border-l-2 ${STATUS_BORDER[status]}`}
                />
              )}
            </div>
            <button
              type="button"
              onClick={() => onSelectNode?.(nodeId)}
              aria-label={`Inspect node ${nodeId}, status ${status}`}
              className={`flex items-center gap-3 pb-3 pt-1 min-w-0 text-left rounded px-2 -mx-2 transition-colors ${
                onSelectNode ? "hover:bg-muted/50 cursor-pointer" : "cursor-default"
              } ${nodeId === selectedNode ? "bg-muted" : ""}`}
            >
              <span
                className={`font-mono text-sm shrink-0 ${
                  nodeId === currentNode ? "font-semibold text-foreground" : "text-foreground"
                }`}
              >
                {nodeId}
              </span>
              {agent && (
                // Secondary to the node name: smaller, lighter, parenthesised.
                <span className="truncate text-[11px] text-muted-foreground/60">
                  ({agent.name} · {agent.runtime})
                </span>
              )}
              {elapsed != null && (
                <span className="text-xs text-muted-foreground tabular-nums shrink-0" suppressHydrationWarning>
                  {formatDuration(elapsed)}
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
