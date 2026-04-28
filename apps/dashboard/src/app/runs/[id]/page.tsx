"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Pause, Play, Square } from "lucide-react";
import {
  useAbortRun,
  useAgentsList,
  usePauseRun,
  useResumeRun,
  useRun,
  usePipeline,
} from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RunStatusBadge } from "@/components/status-badge";
import { PipelineGraph } from "@/components/pipeline-graph";
import { NodeDrawer } from "@/components/node-drawer";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import type { Agent, Run } from "@/lib/api/types";

export default function RunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: run, isPending, isError, error } = useRun(id);
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
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
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
          <RunActions run={run} />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4 text-xs">
        <Metric label="Pipeline" value={`${run.pipeline_id.slice(0, 8)}… v${run.pipeline_version}`} />
        <Metric label="Trigger" value={run.trigger_source} />
        <Metric label="Tokens" value={formatTokens(run.tokens_used)} />
        <Metric label="Cost" value={formatCost(run.cost_usd)} />
      </div>
      <div className="grid grid-cols-3 gap-4 text-xs">
        <Metric label="Started" value={new Date(run.started_at).toLocaleString()} />
        <Metric
          label="Ended"
          value={run.ended_at ? new Date(run.ended_at).toLocaleString() : "—"}
        />
        <Metric label="Duration" value={formatDuration(duration)} />
      </div>

      {pipeline ? (
        <PipelineGraph
          pipeline={pipeline}
          agents={agents}
          nodeStatuses={run.node_statuses}
          currentNode={run.current_node}
          onNodeClick={(nodeId) => setSelectedNode(nodeId)}
        />
      ) : (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Loading pipeline…
          </CardContent>
        </Card>
      )}

      <NodeDrawer
        runId={run.id}
        nodeId={selectedNode}
        onOpenChange={(open) => !open && setSelectedNode(null)}
      />
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border bg-background p-3">
      <div className="text-muted-foreground">{label}</div>
      <div className="font-medium font-mono">{value}</div>
    </div>
  );
}

function RunActions({ run }: { run: Run }) {
  const pause = usePauseRun();
  const resume = useResumeRun();
  const abort = useAbortRun();

  const status = run.final_status;
  if (status !== "running" && status !== "paused") {
    return null;
  }

  const busy = pause.isPending || resume.isPending || abort.isPending;
  const lastError = pause.error ?? resume.error ?? abort.error;

  const handleAbort = () => {
    if (window.confirm("Abort this run? This cannot be undone.")) {
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
