"use client";

/**
 * Workflow-binding panel on the project detail page (audit D1 slice 6).
 *
 * Post-split this file only orchestrates: it owns the
 * ``useUpdateProject`` mutation, derives the recommended /
 * custom-kind partition, and renders one ``WorkflowCard`` per kind
 * plus the ``AddCustomKind`` form. The per-card UI (binding,
 * trigger, issue picker, errors) lives in sibling modules.
 *
 * Pre-split this file was 469 LOC bundling four components; this
 * orchestrator now stays under 100.
 */

import { useUpdateProject, usePipelinesList } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import {
  RECOMMENDED_PIPELINE_KINDS,
  type Project,
} from "@/lib/api/types";

import { AddCustomKind } from "./workflow-cards/add-custom-kind";
import { WorkflowCard } from "./workflow-cards/workflow-card";


interface WorkflowCardsProps {
  project: Project;
}


export function WorkflowCards({ project }: WorkflowCardsProps) {
  const update = useUpdateProject();
  const { data: pipelinesData } = usePipelinesList();
  const pipelines = pipelinesData?.items ?? [];

  const setBinding = async (kind: string, pipelineId: string | null) => {
    const next = { ...project.pipelines };
    if (pipelineId) {
      next[kind] = pipelineId;
    } else {
      delete next[kind];
    }
    await update.mutateAsync({
      id: project.id,
      payload: {
        name: project.name,
        description: project.description,
        working_directory: project.working_directory,
        repo_url: project.repo_url,
        default_branch: project.default_branch,
        pipelines: next,
        env_vars: project.env_vars,
      },
    });
  };

  // Custom kinds = whatever the user added that isn't in the
  // recommended list. Render them in declaration order (insertion
  // order on the dict from the server).
  const customKinds = Object.keys(project.pipelines).filter(
    (k) => !(RECOMMENDED_PIPELINE_KINDS as readonly string[]).includes(k),
  );

  return (
    <div className="space-y-3">
      {update.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {formatApiError(update.error)}
        </p>
      ) : null}

      {RECOMMENDED_PIPELINE_KINDS.map((kind) => (
        <WorkflowCard
          key={kind}
          kind={kind}
          recommended
          project={project}
          pipelines={pipelines}
          isUpdating={update.isPending}
          onBind={(pid) => setBinding(kind, pid)}
        />
      ))}

      {customKinds.map((kind) => (
        <WorkflowCard
          key={kind}
          kind={kind}
          recommended={false}
          project={project}
          pipelines={pipelines}
          isUpdating={update.isPending}
          onBind={(pid) => setBinding(kind, pid)}
          onRemoveKind={() => setBinding(kind, null)}
        />
      ))}

      <AddCustomKind project={project} />
    </div>
  );
}
