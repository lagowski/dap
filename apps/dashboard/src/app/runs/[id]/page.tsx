"use client";

import { use, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useRun, usePipeline } from "@/hooks/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RunStatusBadge } from "@/components/status-badge";
import { PipelineGraph } from "@/components/pipeline-graph";
import { NodeDrawer } from "@/components/node-drawer";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";

export default function RunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: run, isPending, isError, error } = useRun(id);
  const { data: pipeline } = usePipeline(run?.pipeline_id ?? null);

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
