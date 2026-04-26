"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import { useTriggerRun, usePipelineVersions } from "@/hooks/api";
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

interface TriggerRunDialogProps {
  pipelineId: string;
  pipelineName: string;
  currentVersion: number;
  /** Render-prop: parent provides the trigger button. */
  children: (open: () => void) => React.ReactNode;
}

const PLACEHOLDER = '{"repo": "my-repo", "branch": "main"}';

export function TriggerRunDialog({
  pipelineId,
  pipelineName,
  currentVersion,
  children,
}: TriggerRunDialogProps) {
  const router = useRouter();
  const trigger = useTriggerRun();
  const versions = usePipelineVersions(pipelineId);

  const [open, setOpen] = useState(false);
  const [versionStr, setVersionStr] = useState<string>("current");
  const [stateText, setStateText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setParseError(null);

    let initialState: Record<string, unknown> = {};
    if (stateText.trim().length > 0) {
      try {
        initialState = JSON.parse(stateText);
        if (typeof initialState !== "object" || initialState == null || Array.isArray(initialState)) {
          throw new Error("initial_state must be a JSON object");
        }
      } catch (err) {
        setParseError(err instanceof Error ? err.message : "Invalid JSON");
        return;
      }
    }

    const pipelineVersion =
      versionStr === "current" ? undefined : Number(versionStr);

    trigger.mutate(
      {
        pipeline_id: pipelineId,
        ...(pipelineVersion != null ? { pipeline_version: pipelineVersion } : {}),
        initial_state: initialState,
      },
      {
        onSuccess: (run) => {
          setOpen(false);
          setStateText("");
          router.push(`/runs/${run.id}`);
        },
      },
    );
  };

  const submitError = trigger.error ? formatApiError(trigger.error) : null;

  return (
    <>
      {children(() => setOpen(true))}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Run pipeline</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{pipelineName}</span>
              {" — starts a new run with the supplied initial state."}
            </DialogDescription>
          </DialogHeader>

          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="pipeline-version">Pipeline version</Label>
              <select
                id="pipeline-version"
                className="w-full h-9 rounded-md border bg-background px-3 text-sm"
                value={versionStr}
                onChange={(e) => setVersionStr(e.target.value)}
                disabled={versions.isPending}
              >
                <option value="current">current (v{currentVersion})</option>
                {versions.data
                  ?.filter((v) => v.version !== currentVersion)
                  .sort((a, b) => b.version - a.version)
                  .map((v) => (
                    <option key={v.version} value={String(v.version)}>
                      v{v.version}
                    </option>
                  ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="initial-state">Initial state (JSON, optional)</Label>
              <Textarea
                id="initial-state"
                placeholder={PLACEHOLDER}
                value={stateText}
                onChange={(e) => setStateText(e.target.value)}
                rows={8}
                className="font-mono text-xs"
              />
              <p className="text-xs text-muted-foreground">
                Merged over PipelineState defaults. Leave empty for an empty state.
              </p>
            </div>

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
                onClick={() => setOpen(false)}
                disabled={trigger.isPending}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={trigger.isPending}>
                <Play className="mr-1 h-3.5 w-3.5" />
                {trigger.isPending ? "Starting…" : "Run"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
