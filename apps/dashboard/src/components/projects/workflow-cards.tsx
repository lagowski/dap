"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, GitBranch, Play, Plus, X } from "lucide-react";
import {
  useTriggerProjectRun,
  useUpdateProject,
  usePipelinesList,
  useProjectIssues,
} from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
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
  const [showIssuePicker, setShowIssuePicker] = useState(false);

  // Show issue picker for cortex-style pipelines when repo_url is set.
  const canPickIssue = !!project.repo_url && kind === "cortex";

  const handleTrigger = async (issueNumber?: number, issueTitle?: string, issueUrl?: string, issueBody?: string) => {
    if (!boundId) return;
    try {
      // Extract owner/repo from repo_url for initial_state
      const repoMatch = project.repo_url?.match(/[:/]([^/:]+\/[^/]+?)(?:\.git)?$/);
      const repo = repoMatch?.[1];

      const payload = issueNumber && repo ? {
        initial_state: {
          run_id: `${project.name}-${issueNumber}`,
          repo,
          branch: project.default_branch,
          extensions: {
            issue_number: issueNumber,
            issue_url: issueUrl ?? `https://github.com/${repo}/issues/${issueNumber}`,
            issue_title: issueTitle ?? "",
            issue_body: (issueBody ?? "").slice(0, 1000),
            workspace: project.working_directory ?? "",
          },
        },
      } : undefined;

      const run = await trigger.mutateAsync({ id: project.id, kind, payload });
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
          <Badge variant="secondary" className="text-[10px]">recommended</Badge>
        ) : (
          <Badge variant="outline" className="text-[10px]">custom</Badge>
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
          onClick={() => canPickIssue ? setShowIssuePicker(true) : handleTrigger()}
          title={boundId ? "" : "Bind a pipeline first"}
        >
          <Play className="h-3.5 w-3.5 mr-1" />
          {trigger.isPending ? "Triggering…" : "Trigger"}
        </Button>
      </div>

      {showIssuePicker && (
        <IssuePicker
          project={project}
          onSelect={(num, title, url, body) => {
            setShowIssuePicker(false);
            handleTrigger(num, title, url, body);
          }}
          onCancel={() => setShowIssuePicker(false)}
        />
      )}

      {boundPipeline ? (
        <p className="text-xs text-muted-foreground">
          Pipeline: <span className="font-mono">{boundPipeline.id.slice(0, 8)}…</span>
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

function IssuePicker({
  project,
  onSelect,
  onCancel,
}: {
  project: Project;
  onSelect: (number: number, title: string, url: string, body: string) => void;
  onCancel: () => void;
}) {
  const { data: issues, isPending, isError } = useProjectIssues(project.id);

  return (
    <Card className="border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30">
      <CardContent className="pt-3 pb-3 space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold flex items-center gap-1">
            <GitBranch className="h-3.5 w-3.5" />
            Select an issue to implement
          </p>
          <Button type="button" variant="ghost" size="sm" className="h-6 px-1" onClick={onCancel}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>

        {isPending && (
          <p className="text-xs text-muted-foreground">Loading issues…</p>
        )}
        {isError && (
          <p className="text-xs text-destructive flex items-center gap-1">
            <AlertCircle className="h-3.5 w-3.5" />
            Could not fetch issues — check repo_url and GitHub token
          </p>
        )}
        {issues && issues.length === 0 && (
          <p className="text-xs text-muted-foreground italic">No open issues found.</p>
        )}
        {issues && issues.length > 0 && (
          <ul className="space-y-1 max-h-64 overflow-y-auto">
            {issues.map((issue) => (
              <li key={issue.number}>
                <button
                  type="button"
                  className="w-full text-left rounded px-2 py-1.5 hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors"
                  onClick={() => onSelect(issue.number, issue.title, issue.url, issue.body)}
                >
                  <div className="flex items-start gap-2">
                    <span className="text-xs font-mono text-muted-foreground shrink-0 mt-0.5">
                      #{issue.number}
                    </span>
                    <div className="min-w-0">
                      <p className="text-xs font-medium truncate">{issue.title}</p>
                      {issue.labels.length > 0 && (
                        <div className="flex gap-1 mt-0.5 flex-wrap">
                          {issue.labels.map((l) => (
                            <span key={l} className="text-[10px] bg-blue-200 dark:bg-blue-800 rounded px-1">
                              {l}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
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
