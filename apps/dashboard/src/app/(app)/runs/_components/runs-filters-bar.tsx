"use client";

import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { FinalStatus, Pipeline, Project } from "@/lib/api/types";
import {
  AD_HOC_PROJECT_VALUE,
  ALL_PROJECTS_VALUE,
  RUN_ID_PREFIX_LENGTH,
  RUN_STATUSES,
} from "./runs-filters";

interface RunsFiltersBarProps {
  fromDate: string;
  pipelineOptions: Pipeline[];
  projectById: ReadonlyMap<string, Project>;
  projects: Project[];
  selectedPipelineId?: string;
  selectedProjectId?: string;
  selectedProjectValue: string;
  selectedStatuses: FinalStatus[];
  toDate: string;
  updateQuery: (mutate: (params: URLSearchParams) => void) => void;
}

export function RunsFiltersBar({
  fromDate,
  pipelineOptions,
  projectById,
  projects,
  selectedPipelineId,
  selectedProjectId,
  selectedProjectValue,
  selectedStatuses,
  toDate,
  updateQuery,
}: RunsFiltersBarProps) {
  return (
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
  );
}
