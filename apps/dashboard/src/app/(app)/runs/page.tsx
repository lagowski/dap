"use client";

import { Suspense, useMemo } from "react";
import { Play } from "lucide-react";
import { useApproveGate, usePipelinesList, useProject, useProjectsList, useRunsList } from "@/hooks/api";
import { useActiveProject } from "@/lib/active-project";
import {
  TriggerRunPageDialog,
  useHasPipelines,
} from "@/components/trigger-run-page-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RUN_ID_PREFIX_LENGTH, useRunsFilters } from "./_components/runs-filters";
import { RunsFiltersBar } from "./_components/runs-filters-bar";
import { RunsTable } from "./_components/runs-table";
import { usePausedRunToasts } from "./_components/use-paused-run-toasts";

export default function RunsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading...</div>}>
      <RunsPageContent />
    </Suspense>
  );
}

function RunsPageContent() {
  const { activeProjectId, isHydrated } = useActiveProject();
  const { data: activeProject } = useProject(activeProjectId);
  const { data: projectsData } = useProjectsList();
  const { data: pipelinesData } = usePipelinesList();

  const projects = useMemo(() => projectsData?.items ?? [], [projectsData?.items]);
  const pipelines = useMemo(() => pipelinesData?.items ?? [], [pipelinesData?.items]);
  const filters = useRunsFilters({
    activeProjectId,
    isHydrated,
    projects,
    pipelines,
  });

  // Gate the request on hydration so we don't fire an org-wide query
  // first and then immediately re-fire a scoped one when the
  // persisted activeProjectId comes in. Until hydration ``isPending``
  // stays true and the page renders the existing "Loading…" state.
  const { data, isPending, isError, error, isFetching } = useRunsList(
    filters.runFilters,
    { enabled: isHydrated },
  );
  const { hasPipelines } = useHasPipelines();
  const approveGate = useApproveGate();
  usePausedRunToasts(data?.items);

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

      <RunsFiltersBar
        fromDate={filters.fromDate}
        pipelineOptions={filters.pipelineOptions}
        projectById={filters.projectById}
        projects={projects}
        selectedPipelineId={filters.selectedPipelineId}
        selectedProjectId={filters.selectedProjectId}
        selectedProjectValue={filters.selectedProjectValue}
        selectedStatuses={filters.selectedStatuses}
        toDate={filters.toDate}
        updateQuery={filters.updateQuery}
      />

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
        <RunsTable
          approving={approveGate.isPending}
          onApproveRun={(run) => {
            if (!run.paused_at_node) return;
            approveGate.mutate({
              runId: run.id,
              nodeId: run.paused_at_node,
            });
          }}
          pipelineById={filters.pipelineById}
          projectNameForRun={(run) =>
            run.project_id
              ? filters.projectById.get(run.project_id)?.name ??
                `${run.project_id.slice(0, RUN_ID_PREFIX_LENGTH)}...`
              : "Ad-hoc"
          }
          runs={data.items}
        />
      )}
    </div>
  );
}
