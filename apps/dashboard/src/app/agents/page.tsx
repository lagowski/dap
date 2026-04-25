"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { useAgentsList } from "@/hooks/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const ID_PREFIX = 8;

export default function AgentsPage() {
  const { data, isPending, isError, error } = useAgentsList();

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agents</h1>
        <Button asChild size="sm">
          <Link href="/agents/new">
            <Plus className="h-4 w-4 mr-1" />
            New agent
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
            No agents yet. Create the first one.
          </CardContent>
        </Card>
      )}
      {data && data.items.length > 0 && (
        <Card>
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Role</th>
                <th className="px-4 py-2 font-medium">Runtime</th>
                <th className="px-4 py-2 font-medium">Version</th>
                <th className="px-4 py-2 font-medium">ID</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((agent) => (
                <tr key={agent.id} className="border-b last:border-0 hover:bg-muted/30">
                  <td className="px-4 py-3 font-medium">{agent.name}</td>
                  <td className="px-4 py-3">
                    <Badge variant="secondary">{agent.role}</Badge>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{agent.runtime_id}</td>
                  <td className="px-4 py-3 tabular-nums">v{agent.version}</td>
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                    {agent.id.slice(0, ID_PREFIX)}…
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
