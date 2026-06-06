"use client";

import { use, useMemo, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import Link from "next/link";
import { ArrowLeft, CheckCircle2, Loader2, Pause, Play, RefreshCw, Square, Zap, ZapOff } from "lucide-react";
import {
  useAbortRun,
  useAgentsList,
  useApproveGate,
  useGateCountdown,
  usePauseRun,
  useResumeRun,
  useRun,
  usePipeline,
} from "@/hooks/api";
import { useLiveUpdates } from "@/hooks/use-live-updates";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RunStatusBadge } from "@/components/status-badge";
import { NodeTimeline } from "@/components/node-timeline";
import { PipelineGraph } from "@/components/pipeline-graph";
import { NodeDetailPanel } from "@/components/node-detail-panel";
import { LiveOutputPanel } from "./_components/live-output-panel";
import { useConfirmDestructive } from "@/components/confirm-destructive-dialog";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import type { Agent, FinalStatus, Pipeline, Run } from "@/lib/api/types";

/**
 * Gate for the live-output panel (#662 Phase 3c).
 *
 * The panel — and the SSE subscription it owns — is only meaningful for a
 * run that is still producing output. We mount it for ``running`` /
 * ``paused`` runs, and pass ``enabled`` so the EventSource opens only when
 * the user also has Live updates on. Terminal runs (success/failed/aborted)
 * never mount the panel; toggling Live off keeps it mounted but closes the
 * stream. Exported as a pure function so the gating is unit-testable
 * without rendering the React-19 ``use(params)`` page (see page.test.tsx).
 */
export function liveOutputState(
  finalStatus: FinalStatus,
  live: boolean,
): { show: boolean; enabled: boolean } {
  const nonTerminal = finalStatus === "running" || finalStatus === "paused";
  return { show: nonTerminal, enabled: nonTerminal && live };
}

export default function RunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  // Live auto-refresh is user-toggleable and persisted (#662 Phase 2).
  const [live, setLive] = useLiveUpdates();
  const {
    data: run,
    isPending,
    isError,
    error,
    refetch,
    isFetching,
  } = useRun(id, { live });
  const { data: pipeline } = usePipeline(run?.pipeline_id ?? null);
  // Agents drive the per-edge field-flow chips on the graph (#62).
  // We don't gate the page on it — graph still renders without chips.
  // Memoised so a refetch returning the same data doesn't churn the
  // graph's annotation memo via a fresh array reference.
  const { data: agentsData } = useAgentsList();
  const agents = useMemo<Agent[]>(
    () => agentsData?.items ?? [],
    [agentsData?.items],
  );

  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  if (isPending) {
    return <LoadingState className="p-6" />;
  }
  if (isError) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {(error as Error).message}
          </CardContent>
        </Card>
      </div>
    );
  }

  const duration =
    run.ended_at != null
      ? new Date(run.ended_at).getTime() - new Date(run.started_at).getTime()
      : Date.now() - new Date(run.started_at).getTime();

  const liveOutput = liveOutputState(run.final_status, live);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/runs" aria-label="Back to runs">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold font-mono">{run.id}</h1>
        <RunStatusBadge status={run.final_status} />
        <div className="ml-auto flex items-center gap-2">
          <LiveToggle
            live={live}
            onToggle={setLive}
            onRefresh={() => refetch()}
            isFetching={isFetching}
          />
          <RunActions run={run} pipeline={pipeline ?? null} />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4 text-xs">
        <Metric label="Pipeline" value={`${run.pipeline_id.slice(0, 8)}… v${run.pipeline_version}`} />
        <Metric label="Trigger" value={run.trigger_source} />
        <Metric label="Tokens" value={formatTokens(run.tokens_used)} />
        <Metric label="Cost" value={formatCost(run.cost_usd)} />
      </div>
      <div className="grid grid-cols-3 gap-4 text-xs">
        <Metric label="Started" value={<span suppressHydrationWarning>{new Date(run.started_at).toLocaleString()}</span>} />
        <Metric
          label="Ended"
          value={<span suppressHydrationWarning>{run.ended_at ? new Date(run.ended_at).toLocaleString() : "—"}</span>}
        />
        <Metric label="Duration" value={formatDuration(duration)} />
      </div>

      {run.final_status === "running" && (
        <RunningBanner currentNode={run.current_node} nodeStatuses={run.node_statuses} />
      )}

      {/*
        Live-output panel (#662 Phase 3c). Streams the running node's stdout
        via SSE (EventSource → BFF proxy). Gated by the same Live toggle as
        polling: only mounted for non-terminal runs, and the EventSource is
        opened only when Live is on (``enabled``) — so it isn't created for
        finished runs or when the user paused live updates.
      */}
      {liveOutput.show && (
        <LiveOutputPanel runId={run.id} enabled={liveOutput.enabled} />
      )}

      {Object.keys(run.node_statuses).length > 0 && (
        <NodeTimeline
          nodeStatuses={run.node_statuses}
          currentNode={run.current_node}
          runId={run.id}
          selectedNode={selectedNode}
          onSelectNode={(nodeId) => setSelectedNode(nodeId)}
        />
      )}

      {pipeline ? (
        <PipelineGraph
          pipeline={pipeline}
          agents={agents}
          nodeStatuses={run.node_statuses}
          currentNode={run.current_node}
          onNodeClick={(nodeId) => setSelectedNode(nodeId)}
          autoLayout
        />
      ) : (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Loading pipeline…
          </CardContent>
        </Card>
      )}

      {run.final_status === "paused" && run.paused_at_node && (
        <GatePanel run={run} pipeline={pipeline ?? null} />
      )}

      <NodeDetailPanel
        runId={run.id}
        nodeId={selectedNode}
        nodeIds={Object.keys(run.node_statuses)}
        onOpenChange={(open) => !open && setSelectedNode(null)}
        onSelectNode={(nodeId) => setSelectedNode(nodeId)}
      />
    </div>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded border bg-background p-3">
      <div className="text-muted-foreground">{label}</div>
      <div className="font-medium font-mono">{value}</div>
    </div>
  );
}

export function LiveToggle({
  live,
  onToggle,
  onRefresh,
  isFetching,
}: {
  live: boolean;
  onToggle: (v: boolean) => void;
  onRefresh: () => void;
  isFetching: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      {!live && (
        <>
          <span className="text-xs text-muted-foreground">Live updates paused</span>
          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            disabled={isFetching}
            aria-label="Refresh run"
          >
            <RefreshCw
              className={`mr-1 h-3.5 w-3.5${isFetching ? " animate-spin" : ""}`}
            />
            Refresh
          </Button>
        </>
      )}
      <Button
        variant={live ? "outline" : "ghost"}
        size="sm"
        role="switch"
        aria-checked={live}
        aria-label="Toggle live updates"
        onClick={() => onToggle(!live)}
      >
        {live ? (
          <Zap className="mr-1 h-3.5 w-3.5 text-blue-500" />
        ) : (
          <ZapOff className="mr-1 h-3.5 w-3.5" />
        )}
        Live
      </Button>
    </div>
  );
}

function RunActions({ run, pipeline }: { run: Run; pipeline: Pipeline | null }) {
  const pause = usePauseRun();
  const resume = useResumeRun();
  const abort = useAbortRun();
  const confirmDestructive = useConfirmDestructive();

  const status = run.final_status;
  if (status !== "running" && status !== "paused") {
    return null;
  }

  const busy = pause.isPending || resume.isPending || abort.isPending;
  const lastError = pause.error ?? resume.error ?? abort.error;

  const approvalNodes = new Set(pipeline?.defaults?.approval_required_nodes ?? []);
  const gateNode = run.paused_at_node ?? null;
  const isAtGate = status === "paused" && gateNode != null && approvalNodes.has(gateNode);

  const handleAbort = async () => {
    const ok = await confirmDestructive({
      title: "Abort run",
      description: "Abort this run? This cannot be undone.",
      confirmLabel: "Abort",
    });
    if (ok) {
      abort.mutate(run.id);
    }
  };

  return (
    <>
      {lastError ? (
        <span className="text-xs text-destructive" role="alert">
          {formatApiError(lastError)}
        </span>
      ) : null}
      {status === "running" ? (
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => pause.mutate(run.id)}
        >
          <Pause className="mr-1 h-3.5 w-3.5" />
          Pause
        </Button>
      ) : isAtGate ? (
        // GatePanel renders its own Approve button — don't duplicate here.
        null
      ) : (
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => resume.mutate(run.id)}
        >
          <Play className="mr-1 h-3.5 w-3.5" />
          Resume
        </Button>
      )}
      <Button
        variant="destructive"
        size="sm"
        disabled={busy}
        onClick={handleAbort}
      >
        <Square className="mr-1 h-3.5 w-3.5" />
        Abort
      </Button>
    </>
  );
}

function RunningBanner({
  currentNode,
  nodeStatuses,
}: {
  currentNode: string | null;
  nodeStatuses: Record<string, string>;
}) {
  const completedCount = Object.values(nodeStatuses).filter((s) => s === "success").length;
  const totalCount = Object.keys(nodeStatuses).length;

  return (
    <div className="flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30 px-3 py-2 text-sm">
      <Loader2 className="h-4 w-4 animate-spin text-blue-500 shrink-0" />
      <span className="font-medium text-blue-700 dark:text-blue-300">
        {currentNode ? (
          <>Running <span className="font-mono">{currentNode}</span>…</>
        ) : (
          "Pipeline running…"
        )}
      </span>
      {totalCount > 0 && (
        <span className="ml-auto text-xs text-blue-600 dark:text-blue-400">
          {completedCount}/{totalCount} nodes complete
        </span>
      )}
    </div>
  );
}

function GatePanel({ run, pipeline }: { run: Run; pipeline: Pipeline | null }) {
  const approve = useApproveGate();
  const abort = useAbortRun();
  const confirmDestructive = useConfirmDestructive();
  const countdown = useGateCountdown(run.gate_expires_at);

  const gateNode = run.paused_at_node!;
  // Show Approve whenever paused_at_node is set — the backend already
  // validated it's a gate. Only hide if the pipeline loaded AND explicitly
  // says it isn't a gate (guards against stale/drifted pipeline defaults).
  const approvalNodes = new Set(pipeline?.defaults?.approval_required_nodes ?? []);
  const showApprove = pipeline == null || approvalNodes.has(gateNode);
  const assignments = run.gate_payload?.task_assignments ?? [];

  const busy = approve.isPending || abort.isPending;

  const handleAbort = async () => {
    const ok = await confirmDestructive({
      title: "Abort run",
      description: "Abort this run? This cannot be undone.",
      confirmLabel: "Abort",
    });
    if (ok) {
      abort.mutate(run.id);
    }
  };

  // Optimistic: once approved, show a resuming state immediately without
  // waiting for the next poll to confirm final_status=running.
  if (approve.isPending) {
    return (
      <Card className="border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30">
        <CardContent className="pt-4 pb-4 flex items-center gap-2 text-sm text-blue-700 dark:text-blue-300">
          <Loader2 className="h-4 w-4 animate-spin shrink-0" />
          Resuming pipeline…
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30">
      <CardContent className="pt-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold">
              Paused — waiting for approval
            </p>
            <p className="text-xs text-muted-foreground font-mono mt-0.5">
              gate: {gateNode}
            </p>
            {countdown.label && (
              <p className={`text-xs mt-0.5 font-medium${countdown.isUrgent ? " text-red-600 dark:text-red-400" : " text-amber-700 dark:text-amber-400"}`}>
                expires in {countdown.label}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            {showApprove && (
              <Button
                size="sm"
                disabled={busy}
                onClick={() => approve.mutate({ runId: run.id, nodeId: gateNode })}
              >
                <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
                Approve
              </Button>
            )}
            <Button
              variant="destructive"
              size="sm"
              disabled={busy}
              onClick={handleAbort}
            >
              <Square className="mr-1 h-3.5 w-3.5" />
              Abort
            </Button>
          </div>
        </div>

        {assignments.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Ready to implement
            </p>
            <ul className="space-y-1.5">
              {assignments.map((a, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <Play className="h-3 w-3 mt-0.5 shrink-0 text-muted-foreground" />
                  <span className="flex-1">{a.task}</span>
                  <span className="text-xs font-mono text-muted-foreground shrink-0">
                    {a.agent}
                    {a.priority ? ` [${a.priority}]` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {approve.error && (
          <p className="text-xs text-destructive" role="alert">
            {formatApiError(approve.error)}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
