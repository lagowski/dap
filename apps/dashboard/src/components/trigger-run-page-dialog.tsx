"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import {
  usePipelinesList,
  useTriggerRun,
  usePipelineVersions,
  useProjectsList,
} from "@/hooks/api";
import { useActiveProject } from "@/lib/active-project";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const PLACEHOLDER = '{"repo": "my-repo", "branch": "main"}';

/**
 * Trigger-run dialog used on the /runs page. Unlike `TriggerRunDialog` which
 * takes a fixed pipelineId, this variant lets the user pick a pipeline from
 * the full list.
 */
export function TriggerRunPageDialog({
  children,
}: {
  children: (open: () => void) => React.ReactNode;
}) {
  const router = useRouter();
  const { activeProjectId } = useActiveProject();
  const trigger = useTriggerRun();
  const pipelines = usePipelinesList();
  const projects = useProjectsList();

  const [open, setOpen] = useState(false);
  const [selectedPipelineId, setSelectedPipelineId] = useState<string>("");
  const [projectId, setProjectId] = useState<string>(activeProjectId ?? "");
  const [versionStr, setVersionStr] = useState<string>("latest");
  const [stateText, setStateText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const versions = usePipelineVersions(selectedPipelineId || null, {
    enabled: open && selectedPipelineId !== "",
  });

  const selectedPipeline = pipelines.data?.items.find(
    (p) => p.id === selectedPipelineId,
  );

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setParseError(null);
      trigger.reset();
    } else {
      // Reset project to active on every open
      setProjectId(activeProjectId ?? "");
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setParseError(null);

    let initialState: Record<string, unknown> = {};
    if (stateText.trim().length > 0) {
      try {
        initialState = JSON.parse(stateText);
        if (
          typeof initialState !== "object" ||
          initialState == null ||
          Array.isArray(initialState)
        ) {
          throw new Error("initial_state must be a JSON object");
        }
      } catch (err) {
        setParseError(err instanceof Error ? err.message : "Invalid JSON");
        return;
      }
    }

    const parsedVersion =
      versionStr === "latest" ? undefined : Number(versionStr);
    const pipelineVersion =
      parsedVersion !== undefined && Number.isFinite(parsedVersion)
        ? parsedVersion
        : undefined;

    trigger.mutate(
      {
        pipeline_id: selectedPipelineId,
        ...(pipelineVersion !== undefined
          ? { pipeline_version: pipelineVersion }
          : {}),
        ...(projectId ? { project_id: projectId } : {}),
        initial_state: initialState,
      },
      {
        onSuccess: (run) => {
          handleOpenChange(false);
          setSelectedPipelineId("");
          setStateText("");
          router.push(`/runs/${run.id}`);
        },
      },
    );
  };

  const submitError = trigger.error ? formatApiError(trigger.error) : null;
  const zeroPipelines =
    pipelines.data != null && pipelines.data.items.length === 0;

  return (
    <>
      {children(() => setOpen(true))}
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Trigger run</DialogTitle>
            <DialogDescription>
              Select a pipeline and start a new run.
            </DialogDescription>
          </DialogHeader>

          {zeroPipelines ? (
            <div className="space-y-3 py-2">
              <p className="text-sm text-muted-foreground">
                No pipelines found. Create a pipeline first before triggering a
                run.
              </p>
              <Button asChild>
                <Link href="/pipelines/new">Create pipeline</Link>
              </Button>
            </div>
          ) : (
            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="space-y-1.5">
                <Label htmlFor="trigger-pipeline">Pipeline</Label>
                <select
                  id="trigger-pipeline"
                  className="w-full h-9 rounded-md border bg-background px-3 text-sm"
                  value={selectedPipelineId}
                  onChange={(e) => {
                    setSelectedPipelineId(e.target.value);
                    setVersionStr("latest");
                  }}
                  disabled={pipelines.isPending}
                >
                  <option value="" disabled>
                    Select a pipeline…
                  </option>
                  {pipelines.data?.items.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} (v{p.version})
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="trigger-project">Project (optional)</Label>
                <select
                  id="trigger-project"
                  className="w-full h-9 rounded-md border bg-background px-3 text-sm"
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                  disabled={projects.isPending}
                >
                  <option value="">Ad-hoc (no project)</option>
                  {projects.data?.items.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>

              {selectedPipeline &&
                versions.data &&
                versions.data.length > 1 && (
                  <div className="space-y-1.5">
                    <Label htmlFor="trigger-version">
                      Pipeline version (optional)
                    </Label>
                    <select
                      id="trigger-version"
                      className="w-full h-9 rounded-md border bg-background px-3 text-sm"
                      value={versionStr}
                      onChange={(e) => setVersionStr(e.target.value)}
                    >
                      <option value="latest">
                        latest (v{selectedPipeline.version})
                      </option>
                      {versions.data
                        .filter(
                          (v) => v.version !== selectedPipeline.version,
                        )
                        .sort((a, b) => b.version - a.version)
                        .map((v) => (
                          <option key={v.version} value={String(v.version)}>
                            v{v.version}
                          </option>
                        ))}
                    </select>
                  </div>
                )}

              <details className="space-y-1.5">
                <summary className="text-sm font-medium cursor-pointer">
                  Initial state (optional)
                </summary>
                <div className="pt-1.5 space-y-1.5">
                  <Textarea
                    id="initial-state"
                    placeholder={PLACEHOLDER}
                    value={stateText}
                    onChange={(e) => setStateText(e.target.value)}
                    rows={6}
                    className="font-mono text-xs"
                  />
                  <p className="text-xs text-muted-foreground">
                    Merged over PipelineState defaults. Leave empty for an empty
                    state.
                  </p>
                </div>
              </details>

              {parseError ? (
                <p className="text-xs text-destructive" role="alert">
                  JSON parse error: {parseError}
                </p>
              ) : null}
              {submitError ? (
                <p className="text-xs text-destructive" role="alert">
                  {submitError}
                </p>
              ) : null}

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => handleOpenChange(false)}
                  disabled={trigger.isPending}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={trigger.isPending || !selectedPipelineId}
                >
                  <Play className="mr-1 h-3.5 w-3.5" />
                  {trigger.isPending ? "Starting…" : "Run"}
                </Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Convenience: does pipelines exist? Used by the runs page header. */
export function useHasPipelines() {
  const pipelines = usePipelinesList();
  return {
    hasPipelines:
      pipelines.data != null && pipelines.data.items.length > 0,
    isLoading: pipelines.isPending,
  };
}
