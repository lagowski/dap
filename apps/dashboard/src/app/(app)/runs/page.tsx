"use client";

import Link from "next/link";
import { Play } from "lucide-react";
import { useProject, useRunsList } from "@/hooks/api";
import { useActiveProject } from "@/lib/active-project";
import { RunStatusBadge } from "@/components/status-badge";
import {
  TriggerRunPageDialog,
  useHasPipelines,
} from "@/components/trigger-run-page-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import type { Run } from "@/lib/api/types";

const RUN_ID_PREFIX_LENGTH = 8;

export default function RunsPage() {
  const { activeProjectId, isHydrated } = useActiveProject();
  const { data: activeProject } = useProject(activeProjectId);
  // Gate the request on hydration so we don't fire an org-wide query
  // first and then immediately re-fire a scoped one when the
  // persisted activeProjectId comes in. Until hydration ``isPending``
  // stays true and the page renders the existing "Loading…" state.
  const { data, isPending, isError, error, isFetching } = useRunsList(
    activeProjectId !== null ? { projectId: activeProjectId } : undefined,
    { enabled: isHydrated },
  );
  const { hasPipelines } = useHasPipelines();

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-2">
          <h1 className="text-2xl font-semibold">Runs</h1>
          {activeProjectId !== null ? (
            <Badge variant="secondary" className="font-normal">
              {activeProject?.name ?? "scoped"}
            </Badge>
          ) : null}
        </div>
        <div className="flex items-center gap-3">
          <p className="text-sm text-muted-foreground">
            {isFetching ? "Refreshing…" : data ? `${data.total} total` : null}
          </p>
          {hasPipelines && (
            <TriggerRunPageDialog>
              {(open) => (
                <Button size="sm" onClick={open}>
                  <Play className="h-3.5 w-3.5 mr-1.5" />
                  Trigger run
                </Button>
              )}
            </TriggerRunPageDialog>
          )}
        </div>
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
        <TriggerRunPageDialog>
          {(open) => (
            <Card
              className="cursor-pointer hover:bg-muted/30 transition-colors"
              onClick={open}
            >
              <CardContent className="pt-6 text-sm text-muted-foreground text-center space-y-3">
                <p>No runs yet.</p>
                <Button size="sm" variant="outline">
                  <Play className="h-3.5 w-3.5 mr-1.5" />
                  Trigger your first run
                </Button>
              </CardContent>
            </Card>
          )}
        </TriggerRunPageDialog>
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
        <span suppressHydrationWarning><span suppressHydrationWarning>{new Date(run.started_at).toLocaleString()}</span></span>
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {formatTokens(run.tokens_used)}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {formatCost(run.cost_usd)}
      </td>
      <td className="px-4 py-3 text-right tabular-nums" suppressHydrationWarning>{formatDuration(duration)}</td>
    </tr>
  );
}
