"use client";

import { useMemo } from "react";
import Link from "next/link";
import { Play, Plus } from "lucide-react";
import { usePipelinesList, useProject } from "@/hooks/api";
import { useActiveProject } from "@/lib/active-project";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TriggerRunDialog } from "@/components/trigger-run-dialog";

const ID_PREFIX = 8;

export default function PipelinesPage() {
  const { data, isPending, isError, error } = usePipelinesList();
  const { activeProjectId } = useActiveProject();
  const { data: activeProject } = useProject(activeProjectId);

  // Map pipeline_id → list of workflow kinds it's bound to in the
  // active project. List, not single value, because the same pipeline
  // could legitimately be bound to multiple kinds (e.g. the same
  // tests pipeline for both ``verify`` and ``release``).
  const boundKindsByPipeline = useMemo<Map<string, string[]>>(() => {
    const out = new Map<string, string[]>();
    if (activeProject) {
      for (const [kind, pipelineId] of Object.entries(activeProject.pipelines)) {
        const existing = out.get(pipelineId) ?? [];
        existing.push(kind);
        out.set(pipelineId, existing);
      }
    }
    return out;
  }, [activeProject]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-2">
          <h1 className="text-2xl font-semibold">Pipelines</h1>
          {activeProjectId !== null && activeProject ? (
            <span className="text-xs text-muted-foreground">
              highlighting bindings in <strong>{activeProject.name}</strong>
            </span>
          ) : null}
        </div>
        <Button asChild size="sm">
          <Link href="/pipelines/new">
            <Plus className="h-4 w-4 mr-1" />
            New pipeline
          </Link>
        </Button>
      </div>

      {isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {isError && (
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {(error as Error).message}
          </CardContent>
        </Card>
      )}
      {data && data.items.length === 0 && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            No pipelines yet. Create the first one.
          </CardContent>
        </Card>
      )}
      {data && data.items.length > 0 && (
        <Card>
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Description</th>
                <th className="px-4 py-2 font-medium">Nodes</th>
                <th className="px-4 py-2 font-medium">Version</th>
                <th className="px-4 py-2 font-medium">ID</th>
                <th className="px-4 py-2 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((pipeline) => {
                const boundKinds = boundKindsByPipeline.get(pipeline.id) ?? [];
                return (
                <tr
                  key={pipeline.id}
                  className="border-b last:border-0 hover:bg-muted/30"
                >
                  <td className="px-4 py-3 font-medium">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span>{pipeline.name}</span>
                      {boundKinds.map((kind) => (
                        <Badge
                          key={kind}
                          variant="info"
                          className="font-mono text-[10px]"
                        >
                          {kind}
                        </Badge>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs max-w-md truncate">
                    {pipeline.description || "—"}
                  </td>
                  <td className="px-4 py-3 tabular-nums">
                    <Badge variant="secondary">{pipeline.nodes.length}</Badge>
                  </td>
                  <td className="px-4 py-3 tabular-nums">v{pipeline.version}</td>
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                    {pipeline.id.slice(0, ID_PREFIX)}…
                  </td>
                  <td className="px-4 py-3 text-right space-x-2">
                    <TriggerRunDialog
                      pipelineId={pipeline.id}
                      pipelineName={pipeline.name}
                      currentVersion={pipeline.version}
                    >
                      {(open) => (
                        <Button variant="outline" size="sm" onClick={open}>
                          <Play className="h-3.5 w-3.5 mr-1" />
                          Run
                        </Button>
                      )}
                    </TriggerRunDialog>
                    <Button asChild variant="outline" size="sm">
                      <Link href={`/pipelines/${pipeline.id}/edit`}>Edit</Link>
                    </Button>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
