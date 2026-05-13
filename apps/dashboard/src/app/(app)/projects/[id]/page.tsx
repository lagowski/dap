"use client";

import { use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, Archive, ArrowLeft, CheckCircle2, GitBranch, Loader2, Pencil, RefreshCw } from "lucide-react";
import { useArchiveProject, useInitWorkspace, useProject, useRunsList, useSyncWorkspace, useWorkspaceStatus } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RunStatusBadge } from "@/components/status-badge";
import { WorkflowCards } from "@/components/projects/workflow-cards";

const ID_PREFIX = 8;
const RUNS_TO_SHOW = 10;

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: project, isPending, isError, error } = useProject(id);
  const archive = useArchiveProject();

  if (isPending) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }
  if (isError) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {formatApiError(error)}
          </CardContent>
        </Card>
      </div>
    );
  }

  const handleArchive = () => {
    if (
      !window.confirm(
        `Archive project "${project.name}"? Triggers will 409, but existing runs keep their project_id.`,
      )
    ) {
      return;
    }
    archive.mutate(project.id, {
      onSuccess: () => router.push("/projects"),
    });
  };

  const envVarKeys = Object.keys(project.env_vars);

  return (
    <div className="p-6 space-y-4 max-w-4xl">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/projects" aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold">{project.name}</h1>
        {!project.is_active ? (
          <Badge variant="destructive">archived</Badge>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          {project.is_active ? (
            <>
              <Button asChild size="sm">
                <Link href={`/projects/${project.id}/edit`}>
                  <Pencil className="h-3.5 w-3.5 mr-1" />
                  Edit
                </Link>
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={archive.isPending}
                onClick={handleArchive}
              >
                <Archive className="h-3.5 w-3.5 mr-1" />
                {archive.isPending ? "Archiving…" : "Archive"}
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {archive.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {formatApiError(archive.error)}
        </p>
      ) : null}

      {project.description ? (
        <p className="text-sm text-muted-foreground">{project.description}</p>
      ) : null}

      <div className="grid grid-cols-4 gap-4 text-xs">
        <Metric
          label="Working dir"
          value={project.working_directory ?? "—"}
          mono
        />
        <Metric label="Repo URL" value={project.repo_url ?? "—"} mono />
        <Metric label="Default branch" value={project.default_branch} mono />
        <Metric label="ID" value={project.id.slice(0, ID_PREFIX) + "…"} mono />
      </div>

      {project.repo_url ? <WorkspaceCard projectId={project.id} /> : null}

      <Card>
        <CardContent className="pt-6 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">Workflows</h2>
            <span className="text-xs text-muted-foreground">
              Bind a pipeline to a kind, then trigger it from this page.
            </span>
          </div>
          <WorkflowCards project={project} />
        </CardContent>
      </Card>

      {envVarKeys.length > 0 ? (
        <Card>
          <CardContent className="pt-6 space-y-2">
            <h2 className="text-sm font-medium">Project env vars</h2>
            <div className="space-y-1 font-mono text-xs">
              {envVarKeys.map((k) => (
                <div key={k} className="flex gap-2">
                  <span className="text-muted-foreground">{k}=</span>
                  <span>{project.env_vars[k]}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      <RecentRuns projectId={project.id} />
    </div>
  );
}

function RecentRuns({ projectId }: { projectId: string }) {
  // Server-side scoped via #64's ``project_id`` query param (wired
  // through ``useRunsList`` in #68). Caches separately from the
  // org-wide list because the queryKey carries ``projectId``.
  const { data, isPending, isError, error } = useRunsList({ projectId });
  const runs = data?.items.slice(0, RUNS_TO_SHOW) ?? [];

  return (
    <Card>
      <CardContent className="pt-6 space-y-2">
        <h2 className="text-sm font-medium">Recent runs</h2>
        {isPending ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : isError ? (
          <p className="text-xs text-destructive">{formatApiError(error)}</p>
        ) : runs.length === 0 ? (
          <p className="text-xs italic text-muted-foreground">
            No runs for this project yet. Trigger a workflow above.
          </p>
        ) : (
          <ul className="divide-y text-sm">
            {runs.map((run) => (
              <li key={run.id} className="py-2 flex items-center gap-2">
                <RunStatusBadge status={run.final_status} />
                <Link
                  href={`/runs/${run.id}`}
                  className="font-mono text-xs hover:underline"
                >
                  {run.id.slice(0, ID_PREFIX)}…
                </Link>
                <span className="text-xs text-muted-foreground">
                  v{run.pipeline_version}
                </span>
                <span className="ml-auto text-xs text-muted-foreground">
                  {new Date(run.started_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function WorkspaceCard({ projectId }: { projectId: string }) {
  const { data: ws, isPending } = useWorkspaceStatus(projectId);
  const init = useInitWorkspace(projectId);
  const sync = useSyncWorkspace(projectId);

  const busy = init.isPending || sync.isPending;

  return (
    <Card>
      <CardContent className="pt-4 pb-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="text-sm font-medium">Workspace</span>
            {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
            {ws?.exists && (
              <span className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                <CheckCircle2 className="h-3.5 w-3.5" />
                {ws.branch}
                {ws.clean === false && (
                  <span className="ml-1 text-amber-500 flex items-center gap-0.5">
                    <AlertTriangle className="h-3 w-3" /> dirty
                  </span>
                )}
              </span>
            )}
            {ws && !ws.exists && !isPending && (
              <span className="text-xs text-destructive flex items-center gap-1">
                <AlertTriangle className="h-3.5 w-3.5" />
                Not initialised
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {ws?.exists ? (
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => sync.mutate()}
                className="h-7 text-xs"
              >
                <RefreshCw className={`h-3 w-3 mr-1 ${sync.isPending ? "animate-spin" : ""}`} />
                Sync
              </Button>
            ) : (
              <Button
                size="sm"
                disabled={busy || isPending}
                onClick={() => init.mutate()}
                className="h-7 text-xs"
              >
                {init.isPending ? (
                  <><Loader2 className="h-3 w-3 mr-1 animate-spin" />Cloning…</>
                ) : (
                  "Initialize Workspace"
                )}
              </Button>
            )}
          </div>
        </div>

        {ws?.exists && ws.last_commit && (
          <p className="mt-1.5 text-xs text-muted-foreground font-mono truncate pl-6">
            {ws.path} · {ws.last_commit}
          </p>
        )}
        {ws && !ws.exists && ws.path && (
          <p className="mt-1 text-xs text-muted-foreground font-mono truncate pl-6">
            Will clone to: {ws.path}
          </p>
        )}

        {(init.isError || sync.isError) && (
          <p className="mt-1 text-xs text-destructive pl-6">
            {formatApiError(init.error ?? sync.error)}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Metric({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded border bg-background p-3">
      <div className="text-muted-foreground">{label}</div>
      <div className={`font-medium truncate ${mono ? "font-mono" : ""}`}>
        {value}
      </div>
    </div>
  );
}
