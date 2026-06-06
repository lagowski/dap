"use client";

import { Fragment, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Archive,
  ChevronDown,
  ChevronRight,
  Pencil,
  Play,
  Plus,
  Upload,
} from "lucide-react";
import {
  useArchivePipeline,
  useImportPipeline,
  useInspectPipelineImportBackends,
  usePipelinesList,
  useProject,
} from "@/hooks/api";
import { useActiveProject } from "@/lib/active-project";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TriggerRunDialog } from "@/components/trigger-run-dialog";
import { useConfirmDestructive } from "@/components/confirm-destructive-dialog";
import { BackendProfileImportDialog } from "@/components/pipelines/backend-profile-import-dialog";
import type {
  BackendProfilesInspectionResponse,
  PipelineExport,
} from "@/lib/api/types";
import {
  bundleHasBackendProfiles,
  hasInspectableProfiles,
} from "@/lib/backend-profile-import";

const ID_PREFIX = 8;

// Split the flat list into "Cortex" (system templates, name starts with
// "Cortex") and "Custom" (everything else the operator made) so the table
// is scannable. Cortex first, Custom second.
const PIPELINE_GROUP_ORDER = ["Cortex", "Custom"] as const;
type PipelineGroup = (typeof PIPELINE_GROUP_ORDER)[number];

function pipelineGroup(name: string): PipelineGroup {
  return /^\s*cortex/i.test(name) ? "Cortex" : "Custom";
}

interface BackendProfileDialogState {
  bundle: PipelineExport;
  inspection: BackendProfilesInspectionResponse;
}

export default function PipelinesPage() {
  const router = useRouter();
  const { data, isPending, isError, error } = usePipelinesList();
  const { activeProjectId } = useActiveProject();
  const { data: activeProject } = useProject(activeProjectId);
  const importPipeline = useImportPipeline();
  const inspectBackends = useInspectPipelineImportBackends();
  const archive = useArchivePipeline();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [backendDialog, setBackendDialog] =
    useState<BackendProfileDialogState | null>(null);
  const confirmDestructive = useConfirmDestructive();

  const handleImportClick = () => {
    setImportError(null);
    fileInputRef.current?.click();
  };

  const handleArchive = async (id: string, name: string) => {
    const ok = await confirmDestructive({
      title: "Archive pipeline",
      description: `Archive pipeline "${name}"? Run history is preserved; the pipeline disappears from the list. Agents it used stay intact.`,
      confirmLabel: "Archive",
    });
    if (!ok) {
      return;
    }
    archive.mutate(id);
  };

  const importBundle = async (bundle: PipelineExport) => {
    const created = await importPipeline.mutateAsync(bundle);
    router.push(`/pipelines/${created.id}/edit`);
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

    if (!isPipelineExportShape(parsed)) {
      setImportError(
        `${file.name} doesn't look like a pipeline export (expected schema_version + pipeline at the top level).`,
      );
      return;
    }

    try {
      if (bundleHasBackendProfiles(parsed)) {
        const inspection = await inspectBackends.mutateAsync(parsed);
        if (hasInspectableProfiles(inspection)) {
          setBackendDialog({ bundle: parsed, inspection });
          return;
        }
      }
      await importBundle(parsed);
    } catch (err) {
      setImportError(formatApiError(err));
    }
  };

  const handleConfiguredImport = async (bundle: PipelineExport) => {
    setImportError(null);
    try {
      await importBundle(bundle);
      setBackendDialog(null);
    } catch (err) {
      setImportError(formatApiError(err));
    }
  };

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

  // Group + order pipelines (Cortex first, Custom second); drop empty groups.
  const groupedPipelines = useMemo(() => {
    const items = data?.items ?? [];
    const by = new Map<PipelineGroup, typeof items>();
    for (const p of items) {
      const g = pipelineGroup(p.name);
      const arr = by.get(g) ?? [];
      arr.push(p);
      by.set(g, arr);
    }
    return PIPELINE_GROUP_ORDER.filter((g) => by.has(g)).map((g) => ({
      group: g,
      items: by.get(g)!,
    }));
  }, [data]);

  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () => new Set(),
  );
  const toggleGroup = (g: string) =>
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) {
        next.delete(g);
      } else {
        next.add(g);
      }
      return next;
    });

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
            disabled={importPipeline.isPending || inspectBackends.isPending}
          >
            <Upload className="h-4 w-4 mr-1" />
            {importPipeline.isPending || inspectBackends.isPending
              ? "Importing…"
              : "Import JSON"}
          </Button>
          <Button asChild size="sm">
            <Link href="/pipelines/new">
              <Plus className="h-4 w-4 mr-1" />
              New pipeline
            </Link>
          </Button>
        </div>
      </div>

      {importError ? (
        <p className="text-sm text-destructive" role="alert">
          Import failed: {importError}
        </p>
      ) : null}

      {archive.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {formatApiError(archive.error)}
        </p>
      ) : null}

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
              {groupedPipelines.map(({ group, items }) => {
                const isCollapsed = collapsedGroups.has(group);
                return (
                  <Fragment key={group}>
                    <tr className="border-b bg-muted/40">
                      <td colSpan={6} className="px-2 py-1.5">
                        <button
                          type="button"
                          onClick={() => toggleGroup(group)}
                          aria-expanded={!isCollapsed}
                          className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
                        >
                          {isCollapsed ? (
                            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                          ) : (
                            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                          )}
                          {group}
                          <Badge variant="secondary" className="ml-1">
                            {items.length}
                          </Badge>
                        </button>
                      </td>
                    </tr>
                    {!isCollapsed &&
                      items.map((pipeline) => {
                        const boundKinds =
                          boundKindsByPipeline.get(pipeline.id) ?? [];
                        return (
                          <tr
                            key={pipeline.id}
                            className="border-b last:border-0 hover:bg-muted/30"
                          >
                            <td className="px-4 py-2 font-medium">
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
                            <td className="px-4 py-2 text-muted-foreground text-xs max-w-xs truncate">
                              {pipeline.description || "—"}
                            </td>
                            <td className="px-4 py-2 tabular-nums">
                              <Badge variant="secondary">
                                {pipeline.nodes.length}
                              </Badge>
                            </td>
                            <td className="px-4 py-2 tabular-nums">
                              v{pipeline.version}
                            </td>
                            <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                              {pipeline.id.slice(0, ID_PREFIX)}…
                            </td>
                            <td className="px-4 py-2 text-right space-x-1 whitespace-nowrap">
                              <TriggerRunDialog
                                pipelineId={pipeline.id}
                                pipelineName={pipeline.name}
                                currentVersion={pipeline.version}
                              >
                                {(open) => (
                                  <Tooltip label="Run pipeline">
                                    <Button
                                      variant="outline"
                                      size="icon"
                                      className="h-8 w-8"
                                      onClick={open}
                                      aria-label="Run pipeline"
                                    >
                                      <Play className="h-3.5 w-3.5" />
                                    </Button>
                                  </Tooltip>
                                )}
                              </TriggerRunDialog>
                              <Tooltip label="Edit pipeline">
                                <Button
                                  asChild
                                  variant="outline"
                                  size="icon"
                                  className="h-8 w-8"
                                >
                                  <Link
                                    href={`/pipelines/${pipeline.id}/edit`}
                                    aria-label="Edit pipeline"
                                  >
                                    <Pencil className="h-3.5 w-3.5" />
                                  </Link>
                                </Button>
                              </Tooltip>
                              <Tooltip label="Archive pipeline">
                                <Button
                                  variant="outline"
                                  size="icon"
                                  className="h-8 w-8"
                                  aria-label="Archive pipeline"
                                  disabled={archive.isPending}
                                  onClick={() =>
                                    handleArchive(pipeline.id, pipeline.name)
                                  }
                                >
                                  <Archive className="h-3.5 w-3.5" />
                                </Button>
                              </Tooltip>
                            </td>
                          </tr>
                        );
                      })}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
      {backendDialog ? (
        <BackendProfileImportDialog
          open
          bundle={backendDialog.bundle}
          inspection={backendDialog.inspection}
          pending={importPipeline.isPending}
          onCancel={() => setBackendDialog(null)}
          onConfirm={handleConfiguredImport}
        />
      ) : null}
    </div>
  );
}

/**
 * Cheap structural check before POSTing — keeps the engine from
 * returning a noisy 422 when the user picked the wrong JSON. The
 * engine does its own ``schema_version`` + payload validation;
 * this guard just catches obviously-wrong files (agent exports,
 * arbitrary JSON, null) before they hit the network.
 */
function isPipelineExportShape(value: unknown): value is PipelineExport {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const candidate = value as { schema_version?: unknown; pipeline?: unknown };
  if (typeof candidate.schema_version !== "string") return false;
  if (
    typeof candidate.pipeline !== "object" ||
    candidate.pipeline === null ||
    Array.isArray(candidate.pipeline)
  ) {
    return false;
  }
  return true;
}
