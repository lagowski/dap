"use client";

import { Suspense, useCallback, useEffect, useMemo } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Play, RotateCcw } from "lucide-react";
import { usePipelinesList, useProject, useProjectsList, useRunsList } from "@/hooks/api";
import { useActiveProject } from "@/lib/active-project";
import { RunStatusBadge } from "@/components/status-badge";
import {
  TriggerRunPageDialog,
  useHasPipelines,
} from "@/components/trigger-run-page-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import type { FinalStatus, Run } from "@/lib/api/types";

const RUN_ID_PREFIX_LENGTH = 8;
const ALL_PROJECTS_VALUE = "all";
const AD_HOC_PROJECT_VALUE = "null";
const RUN_STATUSES: FinalStatus[] = ["running", "paused", "success", "failed", "aborted"];

function isoDateDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export default function RunsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading...</div>}>
      <RunsPageContent />
    </Suspense>
  );
}

function RunsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { activeProjectId, isHydrated } = useActiveProject();
  const { data: activeProject } = useProject(activeProjectId);
  const { data: projectsData } = useProjectsList();
  const { data: pipelinesData } = usePipelinesList();
  const defaultFrom = useMemo(() => isoDateDaysAgo(7), []);

  const projectParam = searchParams.get("project");
  const selectedProjectId =
    projectParam === ALL_PROJECTS_VALUE
      ? undefined
      : projectParam ?? undefined;
  const selectedProjectValue = selectedProjectId ?? ALL_PROJECTS_VALUE;
  const selectedPipelineId = searchParams.get("pipeline") ?? undefined;
  const selectedStatuses = useMemo(
    () =>
      searchParams
        .getAll("status")
        .filter((status): status is FinalStatus =>
          RUN_STATUSES.includes(status as FinalStatus),
        ),
    [searchParams],
  );
  const fromDate = searchParams.get("from") ?? (isHydrated ? defaultFrom : "");
  const toDate = searchParams.get("to") ?? "";

  const projects = useMemo(() => projectsData?.items ?? [], [projectsData?.items]);
  const pipelines = useMemo(() => pipelinesData?.items ?? [], [pipelinesData?.items]);
  const projectById = useMemo(() => new Map(projects.map((p) => [p.id, p])), [projects]);
  const pipelineById = useMemo(
    () => new Map(pipelines.map((pipeline) => [pipeline.id, pipeline])),
    [pipelines],
  );
  const pipelineOptions = useMemo(() => {
    if (!selectedProjectId || selectedProjectId === AD_HOC_PROJECT_VALUE) return pipelines;
    const project = projectById.get(selectedProjectId);
    if (!project) return pipelines;
    const projectPipelineIds = new Set(Object.values(project.pipelines));
    return pipelines.filter((pipeline) => projectPipelineIds.has(pipeline.id));
  }, [pipelines, projectById, selectedProjectId]);

  const updateQuery = useCallback(
    (mutate: (params: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      mutate(params);
      const next = params.toString();
      router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  useEffect(() => {
    if (!isHydrated || projectParam !== null || activeProjectId === null) return;
    updateQuery((params) => {
      params.set("project", activeProjectId);
    });
  }, [activeProjectId, isHydrated, projectParam, updateQuery]);

  const runFilters = useMemo(
    () => ({
      ...(selectedProjectId && selectedProjectId !== ALL_PROJECTS_VALUE
        ? { projectId: selectedProjectId }
        : {}),
      ...(selectedPipelineId ? { pipelineId: selectedPipelineId } : {}),
      ...(selectedStatuses.length > 0 ? { statuses: selectedStatuses } : {}),
      ...(fromDate ? { from: fromDate } : {}),
      ...(toDate ? { to: toDate } : {}),
    }),
    [fromDate, selectedPipelineId, selectedProjectId, selectedStatuses, toDate],
  );

  // Gate the request on hydration so we don't fire an org-wide query
  // first and then immediately re-fire a scoped one when the
  // persisted activeProjectId comes in. Until hydration ``isPending``
  // stays true and the page renders the existing "Loading…" state.
  const { data, isPending, isError, error, isFetching } = useRunsList(
    runFilters,
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

      <div className="rounded-md border bg-background p-3">
        <div className="grid gap-3 lg:grid-cols-[minmax(180px,1fr)_minmax(180px,1fr)_140px_140px_auto]">
          <div className="space-y-1.5">
            <Label htmlFor="runs-project-filter">Project</Label>
            <select
              id="runs-project-filter"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={selectedProjectValue}
              onChange={(event) =>
                updateQuery((params) => {
                  params.set("project", event.target.value);
                  params.delete("pipeline");
                })
              }
            >
              <option value={ALL_PROJECTS_VALUE}>All projects</option>
              <option value={AD_HOC_PROJECT_VALUE}>Ad-hoc runs</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
              {selectedProjectId &&
              selectedProjectId !== AD_HOC_PROJECT_VALUE &&
              !projectById.has(selectedProjectId) ? (
                <option value={selectedProjectId}>
                  {selectedProjectId.slice(0, RUN_ID_PREFIX_LENGTH)}... (unavailable)
                </option>
              ) : null}
            </select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="runs-pipeline-filter">Pipeline</Label>
            <select
              id="runs-pipeline-filter"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={selectedPipelineId ?? ""}
              onChange={(event) =>
                updateQuery((params) => {
                  if (event.target.value) params.set("pipeline", event.target.value);
                  else params.delete("pipeline");
                })
              }
            >
              <option value="">All pipelines</option>
              {pipelineOptions.map((pipeline) => (
                <option key={pipeline.id} value={pipeline.id}>
                  {pipeline.name}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="runs-from-filter">From</Label>
            <Input
              id="runs-from-filter"
              type="date"
              value={fromDate}
              onChange={(event) =>
                updateQuery((params) => {
                  if (event.target.value) params.set("from", event.target.value);
                  else params.delete("from");
                })
              }
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="runs-to-filter">To</Label>
            <Input
              id="runs-to-filter"
              type="date"
              value={toDate}
              onChange={(event) =>
                updateQuery((params) => {
                  if (event.target.value) params.set("to", event.target.value);
                  else params.delete("to");
                })
              }
            />
          </div>

          <div className="flex items-end">
            <Button
              type="button"
              variant="outline"
              className="w-full lg:w-auto"
              onClick={() =>
                updateQuery((params) => {
                  params.set("project", ALL_PROJECTS_VALUE);
                  params.delete("pipeline");
                  params.delete("status");
                  params.delete("from");
                  params.delete("to");
                })
              }
            >
              <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
              Reset
            </Button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted-foreground">Status</span>
          {RUN_STATUSES.map((status) => (
            <label
              key={status}
              className="inline-flex h-8 items-center gap-2 rounded-md border px-2.5 text-sm"
            >
              <input
                type="checkbox"
                checked={selectedStatuses.includes(status)}
                onChange={(event) =>
                  updateQuery((params) => {
                    const next = new Set(selectedStatuses);
                    if (event.target.checked) next.add(status);
                    else next.delete(status);
                    params.delete("status");
                    for (const nextStatus of RUN_STATUSES) {
                      if (next.has(nextStatus)) params.append("status", nextStatus);
                    }
                  })
                }
              />
              {status}
            </label>
          ))}
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
              {data.items.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  projectName={
                    run.project_id
                      ? projectById.get(run.project_id)?.name ??
                        `${run.project_id.slice(0, RUN_ID_PREFIX_LENGTH)}...`
                      : "Ad-hoc"
                  }
                  pipelineName={pipelineById.get(run.pipeline_id)?.name}
                />
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function RunRow({
  run,
  projectName,
  pipelineName,
}: {
  run: Run;
  projectName: string;
  pipelineName?: string;
}) {
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
      <td className="px-4 py-3 text-muted-foreground">{projectName}</td>
      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
        <span className={pipelineName ? "font-sans text-sm text-foreground" : ""}>
          {pipelineName ?? `${run.pipeline_id.slice(0, RUN_ID_PREFIX_LENGTH)}...`}
        </span>
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
