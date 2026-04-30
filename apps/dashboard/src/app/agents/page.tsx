"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, Pencil, Plus, Upload } from "lucide-react";
import { useAgentsList, useArchiveAgent, useImportAgent } from "@/hooks/api";
import { ApiError, formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { AgentExport } from "@/lib/api/types";

const ID_PREFIX = 8;

interface BlockingPipeline {
  id: string;
  name: string;
}

interface ArchiveBlockedDetail {
  message: string;
  blocking_pipelines: BlockingPipeline[];
}

function extractArchiveBlocked(error: unknown): ArchiveBlockedDetail | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const wrapper = error.detail as { detail?: unknown } | null | undefined;
  const inner = wrapper?.detail;
  if (!inner || typeof inner !== "object") return null;
  const candidate = inner as Partial<ArchiveBlockedDetail>;
  if (typeof candidate.message !== "string") return null;
  if (!Array.isArray(candidate.blocking_pipelines)) return null;
  return {
    message: candidate.message,
    blocking_pipelines: candidate.blocking_pipelines.filter(
      (p): p is BlockingPipeline =>
        !!p && typeof p === "object" && typeof (p as BlockingPipeline).id === "string",
    ),
  };
}

export default function AgentsPage() {
  const router = useRouter();
  const { data, isPending, isError, error } = useAgentsList();
  const archive = useArchiveAgent();
  const importAgent = useImportAgent();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const handleArchive = (id: string, name: string) => {
    if (
      !window.confirm(
        `Archive agent "${name}"? It will disappear from pickers and the list. Run history keeps the agent reference intact.`,
      )
    ) {
      return;
    }
    archive.mutate(id);
  };

  const archiveBlocked = extractArchiveBlocked(archive.error);

  const handleImportClick = () => {
    setImportError(null);
    fileInputRef.current?.click();
  };

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Reset the input value so picking the same file twice in a row
    // still triggers ``change`` (otherwise the browser ignores it).
    event.target.value = "";
    if (!file) return;

    let parsed: unknown;
    try {
      const text = await file.text();
      parsed = JSON.parse(text);
    } catch (err) {
      setImportError(
        `Could not read ${file.name} as JSON: ${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }

    if (!isAgentExportShape(parsed)) {
      setImportError(
        `${file.name} doesn't look like an agent export (expected schema_version + agent at the top level).`,
      );
      return;
    }

    try {
      const created = await importAgent.mutateAsync(parsed);
      router.push(`/agents/${created.id}`);
    } catch (err) {
      setImportError(formatApiError(err));
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agents</h1>
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={handleFileChange}
            aria-hidden="true"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleImportClick}
            disabled={importAgent.isPending}
          >
            <Upload className="h-4 w-4 mr-1" />
            {importAgent.isPending ? "Importing…" : "Import JSON"}
          </Button>
          <Button asChild size="sm">
            <Link href="/agents/new">
              <Plus className="h-4 w-4 mr-1" />
              New agent
            </Link>
          </Button>
        </div>
      </div>

      {archive.isError ? (
        archiveBlocked ? (
          <Card className="border-destructive/50">
            <CardContent className="pt-6 pb-4 text-sm space-y-2">
              <p className="text-destructive">{archiveBlocked.message}</p>
              <ul className="list-disc list-inside text-xs text-muted-foreground space-y-0.5">
                {archiveBlocked.blocking_pipelines.map((p) => (
                  <li key={p.id}>
                    <Link
                      href={`/pipelines/${p.id}/edit`}
                      className="hover:underline font-medium text-foreground"
                    >
                      {p.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ) : (
          <p className="text-sm text-destructive" role="alert">
            {formatApiError(archive.error)}
          </p>
        )
      ) : null}

      {importError ? (
        <p className="text-sm text-destructive" role="alert">
          Import failed: {importError}
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
                <th className="px-4 py-2 font-medium">Used in</th>
                <th className="px-4 py-2 font-medium">ID</th>
                <th className="px-4 py-2 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((agent) => {
                const usage = agent.used_in_pipelines ?? 0;
                const blockArchive = usage > 0;
                return (
                  <tr key={agent.id} className="border-b last:border-0 hover:bg-muted/30">
                    <td className="px-4 py-3 font-medium">
                      <Link href={`/agents/${agent.id}`} className="hover:underline">
                        {agent.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="secondary">{agent.role}</Badge>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{agent.runtime_id}</td>
                    <td className="px-4 py-3 tabular-nums">v{agent.version}</td>
                    <td className="px-4 py-3 tabular-nums">
                      {usage === 0 ? (
                        <span className="text-xs text-muted-foreground">—</span>
                      ) : (
                        <Badge variant={blockArchive ? "info" : "secondary"}>
                          {usage} pipeline{usage === 1 ? "" : "s"}
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                      {agent.id.slice(0, ID_PREFIX)}…
                    </td>
                    <td className="px-4 py-3 text-right space-x-2">
                      <Button asChild variant="outline" size="sm">
                        <Link href={`/agents/${agent.id}/edit`}>
                          <Pencil className="h-3.5 w-3.5 mr-1" />
                          Edit
                        </Link>
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={archive.isPending || blockArchive}
                        title={
                          blockArchive
                            ? `Used by ${usage} pipeline${usage === 1 ? "" : "s"} — archive or detach those first`
                            : undefined
                        }
                        onClick={() => handleArchive(agent.id, agent.name)}
                      >
                        <Archive className="h-3.5 w-3.5 mr-1" />
                        Archive
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

/**
 * Cheap structural check before POSTing — keeps the engine from
 * having to reject malformed payloads with a noisy 422 when the user
 * picked a non-export JSON by accident. Server-side validation is
 * still authoritative for content (runtime_id, schema fields, etc.).
 */
function isAgentExportShape(value: unknown): value is AgentExport {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const candidate = value as { schema_version?: unknown; agent?: unknown };
  if (typeof candidate.schema_version !== "string") return false;
  if (
    typeof candidate.agent !== "object" ||
    candidate.agent === null ||
    Array.isArray(candidate.agent)
  ) {
    return false;
  }
  return true;
}
