"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { usePipelinesList } from "@/hooks/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const ID_PREFIX = 8;

export default function PipelinesPage() {
  const { data, isPending, isError, error } = usePipelinesList();

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Pipelines</h1>
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
              {data.items.map((pipeline) => (
                <tr
                  key={pipeline.id}
                  className="border-b last:border-0 hover:bg-muted/30"
                >
                  <td className="px-4 py-3 font-medium">{pipeline.name}</td>
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
                  <td className="px-4 py-3 text-right">
                    <Button asChild variant="outline" size="sm">
                      <Link href={`/pipelines/${pipeline.id}/edit`}>Edit</Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
