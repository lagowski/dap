"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Play, Plus, X } from "lucide-react";
import {
  useTriggerProjectRun,
  useUpdateProject,
  usePipelinesList,
} from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  RECOMMENDED_PIPELINE_KINDS,
  type Pipeline,
  type Project,
} from "@/lib/api/types";

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

interface WorkflowCardProps {
  kind: string;
  recommended: boolean;
  project: Project;
  pipelines: readonly Pipeline[];
  isUpdating: boolean;
  onBind: (pipelineId: string | null) => Promise<void>;
  onRemoveKind?: () => Promise<void>;
}

function WorkflowCard({
  kind,
  recommended,
  project,
  pipelines,
  isUpdating,
  onBind,
  onRemoveKind,
}: WorkflowCardProps) {
  const router = useRouter();
  const trigger = useTriggerProjectRun();
  const boundId = project.pipelines[kind] ?? "";
  const boundPipeline = pipelines.find((p) => p.id === boundId);

  const handleTrigger = async () => {
    if (!boundId) return;
    try {
      const run = await trigger.mutateAsync({ id: project.id, kind });
      router.push(`/runs/${run.id}`);
    } catch {
      // surfaced via trigger.error below
    }
  };

  return (
    <div className="rounded-md border bg-card p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm font-medium">{kind}</span>
        {recommended ? (
          <Badge variant="secondary" className="text-[10px]">
            recommended
          </Badge>
        ) : (
          <Badge variant="outline" className="text-[10px]">
            custom
          </Badge>
        )}
        {!recommended && onRemoveKind ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ml-auto h-7 px-2"
            disabled={isUpdating}
            onClick={onRemoveKind}
            aria-label={`Remove ${kind} workflow kind`}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        ) : null}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={boundId}
          onChange={(e) => onBind(e.target.value || null)}
          disabled={isUpdating}
          aria-label={`Pipeline for ${kind}`}
          className="flex h-9 rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50 min-w-[16rem]"
        >
          <option value="">— Not bound —</option>
          {pipelines.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} (v{p.version})
            </option>
          ))}
        </select>

        <Button
          type="button"
          size="sm"
          disabled={!boundId || trigger.isPending || isUpdating}
          onClick={handleTrigger}
          title={boundId ? "" : "Bind a pipeline first"}
        >
          <Play className="h-3.5 w-3.5 mr-1" />
          {trigger.isPending ? "Triggering…" : "Trigger"}
        </Button>
      </div>

      {boundPipeline ? (
        <p className="text-xs text-muted-foreground">
          Pipeline:{" "}
          <span className="font-mono">{boundPipeline.id.slice(0, 8)}…</span>
        </p>
      ) : (
        <p className="text-xs text-muted-foreground italic">
          No pipeline bound — pick one above to enable trigger.
        </p>
      )}

      {trigger.isError ? (
        <p className="text-xs text-destructive" role="alert">
          {formatApiError(trigger.error)}
        </p>
      ) : null}
    </div>
  );
}

function AddCustomKind({ project }: { project: Project }) {
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
