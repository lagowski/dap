"use client";

import Link from "next/link";
import { useRunsList } from "@/hooks/api";
import { RunStatusBadge } from "@/components/status-badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import type { Run } from "@/lib/api/types";

const RUN_ID_PREFIX_LENGTH = 8;

export default function RunsPage() {
  const { data, isPending, isError, error, isFetching } = useRunsList();

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold">Runs</h1>
        <p className="text-sm text-muted-foreground">
          {isFetching ? "Refreshing…" : data ? `${data.total} total` : null}
        </p>
      </div>

      {isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {isError && (
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            Failed to load runs: {(error as Error).message}
          </CardContent>
        </Card>
      )}

      {data && data.items.length === 0 && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            No runs yet. Trigger one via{" "}
            <code className="font-mono text-xs bg-muted px-1.5 py-0.5 rounded">
              POST /runs
            </code>
            .
          </CardContent>
        </Card>
      )}

      {data && data.items.length > 0 && (
        <Card>
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50">
              <tr className="text-left text-muted-foreground">
                <th className="px-4 py-2 font-medium">Run</th>
                <th className="px-4 py-2 font-medium">Pipeline</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Started</th>
                <th className="px-4 py-2 font-medium text-right">Tokens</th>
                <th className="px-4 py-2 font-medium text-right">Cost</th>
                <th className="px-4 py-2 font-medium text-right">Duration</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function RunRow({ run }: { run: Run }) {
  const duration =
    run.ended_at != null
      ? new Date(run.ended_at).getTime() - new Date(run.started_at).getTime()
      : Date.now() - new Date(run.started_at).getTime();

  return (
    <tr className="border-b last:border-0 hover:bg-muted/30">
      <td className="px-4 py-3 font-mono text-xs">
        <Link
          href={`/runs/${run.id}`}
          className="text-foreground hover:underline"
        >
          {run.id.slice(0, RUN_ID_PREFIX_LENGTH)}…
        </Link>
      </td>
      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
        {run.pipeline_id.slice(0, RUN_ID_PREFIX_LENGTH)}…
        <span className="ml-1 text-foreground">v{run.pipeline_version}</span>
      </td>
      <td className="px-4 py-3">
        <RunStatusBadge status={run.final_status} />
      </td>
      <td className="px-4 py-3 text-muted-foreground text-xs">
        {new Date(run.started_at).toLocaleString()}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {formatTokens(run.tokens_used)}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {formatCost(run.cost_usd)}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">{formatDuration(duration)}</td>
    </tr>
  );
}
