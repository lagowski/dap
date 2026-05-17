"use client";

/**
 * Single workflow card: pipeline binding + Trigger button + (for
 * the ``cortex`` kind on repo-backed projects) the GitHub issue
 * picker overlay. Repeated once per workflow kind by the
 * ``WorkflowCards`` orchestrator.
 *
 * Extracted from ``workflow-cards.tsx`` during the D1 audit split.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Play, X } from "lucide-react";

import { useTriggerProjectRun, useWorkspaceStatus } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Pipeline, Project } from "@/lib/api/types";

import { IssuePicker } from "./issue-picker";


export interface WorkflowCardProps {
  kind: string;
  recommended: boolean;
  project: Project;
  pipelines: readonly Pipeline[];
  isUpdating: boolean;
  onBind: (pipelineId: string | null) => Promise<void>;
  onRemoveKind?: () => Promise<void>;
}


export function WorkflowCard({
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
  const [repoUrlError, setRepoUrlError] = useState<string | null>(null);

  // Workspace must exist before triggering cortex (#371).
  const { data: wsStatus } = useWorkspaceStatus(project.repo_url ? project.id : null);
  const workspaceMissing = project.repo_url != null && wsStatus != null && !wsStatus.exists;

  // Show issue picker for cortex-style pipelines when repo_url is set.
  const canPickIssue = !!project.repo_url && kind === "cortex";

  const handleTrigger = async (
    issueNumber?: number,
    issueTitle?: string,
    issueUrl?: string,
    issueBody?: string,
  ) => {
    if (!boundId) return;
    setRepoUrlError(null);
    try {
      let payload: Parameters<typeof trigger.mutateAsync>[0]["payload"] | undefined;

      if (issueNumber) {
        // Extract owner/repo from repo_url — fail explicitly if unparseable
        // so the user knows to fix the project's repo_url rather than
        // silently running without issue context (#369 Copilot review).
        const repoMatch = project.repo_url?.match(/[:/]([^/:]+\/[^/]+?)(?:\.git)?$/);
        if (!repoMatch) {
          setRepoUrlError(
            `Cannot parse owner/repo from repo_url: "${project.repo_url}". ` +
            "Edit the project and set a valid GitHub URL."
          );
          return;
        }
        const repo = repoMatch[1];
        payload = {
          initial_state: {
            run_id: `${project.name}-${issueNumber}`,
            repo,
            branch: project.default_branch,
            extensions: {
              issue_number: issueNumber,
              issue_url: issueUrl ?? `https://github.com/${repo}/issues/${issueNumber}`,
              issue_title: issueTitle ?? "",
              issue_body: (issueBody ?? "").slice(0, 1000),
              workspace_path: project.working_directory ?? "",
            },
          },
        };
      }

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
          disabled={!boundId || trigger.isPending || isUpdating || workspaceMissing}
          onClick={() => canPickIssue ? setShowIssuePicker(true) : handleTrigger()}
          title={
            !boundId ? "Bind a pipeline first"
            : workspaceMissing ? "Initialize workspace first"
            : ""
          }
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

      {repoUrlError ? (
        <p className="text-xs text-destructive flex items-center gap-1" role="alert">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          {repoUrlError}
        </p>
      ) : null}
      {trigger.isError ? (
        <p className="text-xs text-destructive" role="alert">
          {formatApiError(trigger.error)}
        </p>
      ) : null}
    </div>
  );
}
