"use client";

import Link from "next/link";
import { Archive, Pencil, Plus } from "lucide-react";
import { useArchiveProject, useProjectsList } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useConfirmDestructive } from "@/components/confirm-destructive-dialog";

const ID_PREFIX = 8;

export default function ProjectsPage() {
  const { data, isPending, isError, error } = useProjectsList();
  const archive = useArchiveProject();
  const confirmDestructive = useConfirmDestructive();

  const handleArchive = async (id: string, name: string) => {
    const ok = await confirmDestructive({
      title: "Archive project",
      description: `Archive project "${name}"? Existing runs keep their project_id, but the project won't appear in pickers and triggers will 409.`,
      confirmLabel: "Archive",
    });
    if (!ok) {
      return;
    }
    archive.mutate(id);
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Projects</h1>
        <Button asChild size="sm">
          <Link href="/projects/new">
            <Plus className="h-4 w-4 mr-1" />
            New project
          </Link>
        </Button>
      </div>

      {archive.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {formatApiError(archive.error)}
        </p>
      ) : null}

      {isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {isError && (
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {formatApiError(error)}
          </CardContent>
        </Card>
      )}
      {data && data.items.length === 0 && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            No projects yet. Create the first one to bind pipelines into
            workflows (configure / plan / develop / verify / release).
          </CardContent>
        </Card>
      )}
      {data && data.items.length > 0 && (
        <Card>
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Working dir</th>
                <th className="px-4 py-2 font-medium">Default branch</th>
                <th className="px-4 py-2 font-medium">Bindings</th>
                <th className="px-4 py-2 font-medium">ID</th>
                <th className="px-4 py-2 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((project) => {
                const bindingCount = Object.keys(project.pipelines).length;
                return (
                  <tr
                    key={project.id}
                    className="border-b last:border-0 hover:bg-muted/30"
                  >
                    <td className="p-0 font-medium">
                      <Link
                        href={`/projects/${project.id}`}
                        className="block px-4 py-3"
                      >
                        <span className="hover:underline">{project.name}</span>
                        {project.description ? (
                          <div className="text-xs text-muted-foreground truncate max-w-[24rem]">
                            {project.description}
                          </div>
                        ) : null}
                      </Link>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                      {project.working_directory ?? project.repo_url ?? "—"}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">
                      {project.default_branch}
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={bindingCount > 0 ? "secondary" : "outline"}>
                        {bindingCount} bound
                      </Badge>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                      {project.id.slice(0, ID_PREFIX)}…
                    </td>
                    <td className="px-4 py-3 text-right space-x-1 whitespace-nowrap">
                      <Tooltip label="Edit project">
                        <Button
                          asChild
                          variant="outline"
                          size="icon"
                          className="h-8 w-8"
                        >
                          <Link
                            href={`/projects/${project.id}/edit`}
                            aria-label="Edit project"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Link>
                        </Button>
                      </Tooltip>
                      <Tooltip label="Archive project">
                        <Button
                          variant="outline"
                          size="icon"
                          className="h-8 w-8"
                          aria-label="Archive project"
                          disabled={archive.isPending}
                          onClick={() =>
                            handleArchive(project.id, project.name)
                          }
                        >
                          <Archive className="h-3.5 w-3.5" />
                        </Button>
                      </Tooltip>
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
