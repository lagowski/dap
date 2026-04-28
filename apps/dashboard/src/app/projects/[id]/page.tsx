"use client";

import { use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, ArrowLeft, Pencil } from "lucide-react";
import { useArchiveProject, useProject, useRunsList } from "@/hooks/api";
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
  const { data, isPending, isError, error } = useRunsList({
    pipelineId: undefined,
    finalStatus: undefined,
  });
  // ``useRunsList`` doesn't expose a ``project_id`` filter yet, so we
  // filter client-side on the loaded page. Acceptable for v0.6 since
  // the list is paginated and we only show the top N; #68 will wire
  // server-side scoping in via #64's ``project_id`` query param.
  const runs =
    data?.items
      .filter((r) => r.project_id === projectId)
      .slice(0, RUNS_TO_SHOW) ?? [];

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
