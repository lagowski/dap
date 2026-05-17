"use client";

/**
 * "Add custom workflow" form: collapsed by default, expands into a
 * kind + pipeline picker. Enforces engine rules client-side so the
 * user sees the error before the round-trip: kind must be non-empty,
 * pipeline must be bound (#84), kind can't already exist on the
 * project, kind can't shadow a recommended kind.
 *
 * Extracted from ``workflow-cards.tsx`` during the D1 audit split.
 * Bundles the private ``CustomKindPipelinePicker`` since it has no
 * other caller.
 */

import { useState } from "react";
import { Plus } from "lucide-react";

import { usePipelinesList, useUpdateProject } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  RECOMMENDED_PIPELINE_KINDS,
  type Project,
} from "@/lib/api/types";


export function AddCustomKind({ project }: { project: Project }) {
  const update = useUpdateProject();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pipelineId, setPipelineId] = useState("");

  const cancel = () => {
    setKind("");
    setPipelineId("");
    setError(null);
    setOpen(false);
  };

  // The engine rejects blank pipeline ids (#84 review), so a custom
  // kind always lands on the server with both kind + pipeline_id set.
  const submitWithPipeline = async () => {
    const trimmed = kind.trim();
    setError(null);
    if (!trimmed || !pipelineId) {
      setError("Kind and pipeline are both required");
      return;
    }
    if (project.pipelines[trimmed] !== undefined) {
      setError(`Kind '${trimmed}' already exists on the project`);
      return;
    }
    if ((RECOMMENDED_PIPELINE_KINDS as readonly string[]).includes(trimmed)) {
      setError(`Kind '${trimmed}' is recommended — already shown above`);
      return;
    }
    try {
      await update.mutateAsync({
        id: project.id,
        payload: {
          name: project.name,
          description: project.description,
          working_directory: project.working_directory,
          repo_url: project.repo_url,
          default_branch: project.default_branch,
          pipelines: { ...project.pipelines, [trimmed]: pipelineId },
          env_vars: project.env_vars,
        },
      });
      cancel();
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  if (!open) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
      >
        <Plus className="h-3.5 w-3.5 mr-1" />
        Add custom workflow
      </Button>
    );
  }

  return (
    <div className="rounded-md border border-dashed bg-card p-3 space-y-2">
      <div className="text-xs text-muted-foreground">Add custom workflow kind</div>
      <div className="flex items-end gap-2 flex-wrap">
        <div className="space-y-1">
          <Label htmlFor="custom-kind" className="text-xs">
            Kind
          </Label>
          <Input
            id="custom-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            placeholder="hotfix"
            className="font-mono text-sm h-8 max-w-[12rem]"
          />
        </div>
        <CustomKindPipelinePicker value={pipelineId} onChange={setPipelineId} />
        <div className="flex gap-1">
          <Button
            type="button"
            size="sm"
            disabled={update.isPending}
            onClick={submitWithPipeline}
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={cancel}>
            Cancel
          </Button>
        </div>
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}


function CustomKindPipelinePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const { data } = usePipelinesList();
  const pipelines = data?.items ?? [];
  return (
    <div className="space-y-1">
      <Label htmlFor="custom-pipeline" className="text-xs">
        Pipeline
      </Label>
      <select
        id="custom-pipeline"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="flex h-8 rounded-md border border-input bg-background px-2 text-xs min-w-[14rem]"
      >
        <option value="">— Pick one —</option>
        {pipelines.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} (v{p.version})
          </option>
        ))}
      </select>
    </div>
  );
}
