"use client";

import { Fragment, useMemo, useRef, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Archive,
  ChevronDown,
  ChevronRight,
  Package,
  Pencil,
  Plus,
  Upload,
} from "lucide-react";
import { useAgentsList, useArchiveAgent, useImportAgent } from "@/hooks/api";
import { ApiError, formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useConfirmDestructive } from "@/components/confirm-destructive-dialog";
import { isManagedAgent } from "@/lib/managed-agent";
import { groupAgentsIntoPackages } from "@/lib/agent-packages";
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
  const confirmDestructive = useConfirmDestructive();

  const handleArchive = async (id: string, name: string) => {
    const ok = await confirmDestructive({
      title: "Archive agent",
      description: `Archive agent "${name}"? It will disappear from pickers and the list. Run history keeps the agent reference intact.`,
      confirmLabel: "Archive",
    });
    if (!ok) {
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

  // Managed (Cortex) agents are nodes of an externally-owned bundle (#739), not
  // hand-authored agents — and there are dozens of them with no individual
  // meaning. Present them as a single collapsible *package* instead of flooding
  // the list; standalone agents render as normal rows above it.
  const { standalone, packages } = useMemo(
    () => groupAgentsIntoPackages(data?.items ?? []),
    [data],
  );

  // Packages are collapsed by default (declutter); track which are expanded.
  const [expandedPackages, setExpandedPackages] = useState<Set<string>>(() => new Set());
  const togglePackage = (kind: string) =>
    setExpandedPackages((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) {
        next.delete(kind);
      } else {
        next.add(kind);
      }
      return next;
    });

  const renderRow = (agent: (typeof standalone)[number]) => {
    const usage = agent.used_in_pipelines ?? 0;
    const blockArchive = usage > 0;
    return (
      <tr key={agent.id} className="border-b last:border-0 hover:bg-muted/30">
        <td className="p-0 font-medium">
          <Link
            href={`/agents/${agent.id}`}
            className="flex items-center gap-2 px-4 py-2"
          >
            <span className="hover:underline">{agent.name}</span>
            {isManagedAgent(agent) && (
              <Badge variant="outline" className="font-normal">
                Managed · Cortex
              </Badge>
            )}
          </Link>
        </td>
        <td className="px-4 py-2">
          <Badge variant="secondary">{agent.role}</Badge>
        </td>
        <td className="px-4 py-2 font-mono text-xs">{agent.runtime_id}</td>
        <td className="px-4 py-2 tabular-nums">v{agent.version}</td>
        <td className="px-4 py-2 tabular-nums">
          {usage === 0 ? (
            <span className="text-xs text-muted-foreground">—</span>
          ) : (
            <Badge variant="info">
              {usage} pipeline{usage === 1 ? "" : "s"}
            </Badge>
          )}
        </td>
        <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
          {agent.id.slice(0, ID_PREFIX)}…
        </td>
        <td className="px-4 py-2 text-right space-x-1 whitespace-nowrap">
          <Tooltip label="Edit agent">
            <Button asChild variant="outline" size="icon" className="h-8 w-8">
              <Link href={`/agents/${agent.id}/edit`} aria-label="Edit agent">
                <Pencil className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </Tooltip>
          <Tooltip
            label={
              blockArchive
                ? `Used by ${usage} pipeline${usage === 1 ? "" : "s"} — detach first`
                : "Archive agent"
            }
          >
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              aria-label="Archive agent"
              disabled={archive.isPending || blockArchive}
              onClick={() => handleArchive(agent.id, agent.name)}
            >
              <Archive className="h-3.5 w-3.5" />
            </Button>
          </Tooltip>
        </td>
      </tr>
    );
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agents</h1>
        <div className="flex items-center gap-2">
          {/* sr-only (not display:none) — some Chromium builds refuse to
              open the native file dialog when .click() targets a
              display:none input; an off-screen input fires reliably. */}
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="sr-only"
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

      {isPending && <LoadingState />}
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
          <div className="overflow-x-auto">
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
                {standalone.map(renderRow)}
                {packages.map((pkg) => {
                  const isExpanded = expandedPackages.has(pkg.kind);
                  return (
                    <Fragment key={pkg.kind}>
                      <tr className="border-y bg-muted/40">
                        <td colSpan={7} className="px-2 py-2">
                          <button
                            type="button"
                            onClick={() => togglePackage(pkg.kind)}
                            aria-expanded={isExpanded}
                            className="flex items-center gap-2 text-left hover:text-foreground"
                          >
                            {isExpanded ? (
                              <ChevronDown className="h-4 w-4 shrink-0" aria-hidden />
                            ) : (
                              <ChevronRight className="h-4 w-4 shrink-0" aria-hidden />
                            )}
                            <Package className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                            <span className="font-semibold">{pkg.label}</span>
                            <Badge variant="secondary">
                              {pkg.agents.length} agent{pkg.agents.length === 1 ? "" : "s"}
                            </Badge>
                            <span className="text-xs text-muted-foreground">
                              package · from the {pkg.source} · imported as a unit
                            </span>
                          </button>
                        </td>
                      </tr>
                      {isExpanded && pkg.agents.map(renderRow)}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
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
