"use client";

import Link from "next/link";
import { RunStatusBadge } from "@/components/status-badge";
import { Card } from "@/components/ui/card";
import type { Pipeline, Run } from "@/lib/api/types";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import { RUN_ID_PREFIX_LENGTH } from "./runs-filters";

interface RunsTableProps {
  approving?: boolean;
  onApproveRun: (run: Run) => void;
  pipelineById: ReadonlyMap<string, Pipeline>;
  projectNameForRun: (run: Run) => string;
  runs: Run[];
}

export function RunsTable({
  approving,
  onApproveRun,
  pipelineById,
  projectNameForRun,
  runs,
}: RunsTableProps) {
  return (
    <Card>
      <table className="w-full text-sm">
        <thead className="border-b bg-muted/50">
          <tr className="text-left text-muted-foreground">
            <th className="px-4 py-2 font-medium">Run</th>
            <th className="px-4 py-2 font-medium">Project</th>
            <th className="px-4 py-2 font-medium">Pipeline</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 font-medium">Started</th>
            <th className="px-4 py-2 font-medium text-right">Tokens</th>
            <th className="px-4 py-2 font-medium text-right">Cost</th>
            <th className="px-4 py-2 font-medium text-right">Duration</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <RunRow
              key={run.id}
              run={run}
              projectName={projectNameForRun(run)}
              pipelineName={pipelineById.get(run.pipeline_id)?.name}
              onApprove={
                run.final_status === "paused" && run.paused_at_node
                  ? () => onApproveRun(run)
                  : undefined
              }
              approving={approving}
            />
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function RunRow({
  run,
  projectName,
  pipelineName,
  onApprove,
  approving,
}: {
  run: Run;
  projectName: string;
  pipelineName?: string;
  onApprove?: () => void;
  approving?: boolean;
}) {
  const duration =
    run.ended_at != null
      ? new Date(run.ended_at).getTime() - new Date(run.started_at).getTime()
      : Date.now() - new Date(run.started_at).getTime();

  return (
    <tr className="border-b last:border-0 hover:bg-muted/30">
      <td className="px-4 py-3 font-mono text-xs">
        <Link href={`/runs/${run.id}`} className="text-foreground hover:underline">
          {run.id.slice(0, RUN_ID_PREFIX_LENGTH)}...
        </Link>
      </td>
      <td className="px-4 py-3 text-muted-foreground">{projectName}</td>
      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
        <span className={pipelineName ? "font-sans text-sm text-foreground" : ""}>
          {pipelineName ?? `${run.pipeline_id.slice(0, RUN_ID_PREFIX_LENGTH)}...`}
        </span>
        <span className="ml-1 text-foreground">v{run.pipeline_version}</span>
      </td>
      <td className="px-4 py-3">
        <RunStatusBadge
          status={run.final_status}
          currentNode={run.current_node}
          pausedAtNode={run.paused_at_node}
          gateExpiresAt={run.gate_expires_at}
          onApprove={onApprove}
          approving={approving}
        />
      </td>
      <td className="px-4 py-3 text-muted-foreground text-xs">
        <span suppressHydrationWarning>
          {new Date(run.started_at).toLocaleString()}
        </span>
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {formatTokens(run.tokens_used)}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {formatCost(run.cost_usd)}
      </td>
      <td className="px-4 py-3 text-right tabular-nums" suppressHydrationWarning>
        {formatDuration(duration)}
      </td>
    </tr>
  );
}
