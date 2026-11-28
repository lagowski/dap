"use client";

import { useCallback, useEffect, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { FinalStatus, Pipeline, Project } from "@/lib/api/types";

export const RUN_ID_PREFIX_LENGTH = 8;
export const ALL_PROJECTS_VALUE = "all";
export const AD_HOC_PROJECT_VALUE = "null";
export const RUN_STATUSES: FinalStatus[] = [
  "running",
  "paused",
  "success",
  "failed",
  "aborted",
];

function isoDateDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export function useRunsFilters({
  activeProjectId,
  isHydrated,
  projects,
  pipelines,
}: {
  activeProjectId: string | null;
  isHydrated: boolean;
  projects: Project[];
  pipelines: Pipeline[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const defaultFrom = useMemo(() => isoDateDaysAgo(7), []);

  const projectParam = searchParams.get("project");
  const selectedProjectId =
    projectParam === ALL_PROJECTS_VALUE ? undefined : projectParam ?? undefined;
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

  return {
    fromDate,
    pipelineById,
    pipelineOptions,
    projectById,
    runFilters,
    selectedPipelineId,
    selectedProjectId,
    selectedProjectValue,
    selectedStatuses,
    toDate,
    updateQuery,
  };
}
