"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import {
  useCurrentUser,
  useTriggerRun,
  usePipelineVersions,
  usePipelineReadiness,
} from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import {
  parseInitialState,
  parsePipelineVersion,
  withAutoApprove,
} from "@/lib/run-trigger-options";
import { PipelineReadinessNotice } from "@/components/pipeline-readiness-notice";
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
  const currentUser = useCurrentUser();
  const isAdmin = currentUser.data?.is_superuser === true;

  const [open, setOpen] = useState(false);
  const [versionStr, setVersionStr] = useState<string>("current");
  const [stateText, setStateText] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);

  // Defer the (potentially heavy) versions fetch until the user opens the
  // dialog — the pipelines list page renders one TriggerRunDialog per row.
  const versions = usePipelineVersions(pipelineId, { enabled: open });
  // Pre-run readiness (#710): resolve python-func callables when the dialog
  // opens so a missing package (e.g. dap-cortex) shows here, not as a 422.
  const readiness = usePipelineReadiness(pipelineId, { enabled: open });

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) {
      // Clear transient state so a reopen doesn't show stale errors.
      setParseError(null);
      setAutoApprove(false);
      trigger.reset();
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setParseError(null);

    const parsed = parseInitialState(stateText);
    if (!parsed.ok) {
      setParseError(parsed.error);
      return;
    }
    const pipelineVersion = parsePipelineVersion(versionStr, "current");

    trigger.mutate(
      {
        pipeline_id: pipelineId,
        ...(pipelineVersion !== undefined ? { pipeline_version: pipelineVersion } : {}),
        initial_state: withAutoApprove(parsed.value, isAdmin && autoApprove),
      },
      {
        onSuccess: (run) => {
          handleOpenChange(false);
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
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Run pipeline</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{pipelineName}</span>
              {" — starts a new run with the supplied initial state."}
            </DialogDescription>
          </DialogHeader>

          <form className="space-y-4" onSubmit={handleSubmit}>
            <PipelineReadinessNotice readiness={readiness.data} />

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

            {isAdmin ? (
              <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-primary"
                  checked={autoApprove}
                  aria-describedby="trigger-auto-approve-description"
                  onChange={(event) => setAutoApprove(event.target.checked)}
                />
                <span>
                  <span className="font-medium">
                    Skip all approval gates for this run
                  </span>
                  <span
                    id="trigger-auto-approve-description"
                    className="block text-xs text-muted-foreground"
                  >
                    Sets extensions.auto_approve for this trigger only.
                  </span>
                </span>
              </label>
            ) : null}

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
