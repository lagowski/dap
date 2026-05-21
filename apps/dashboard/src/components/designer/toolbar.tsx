"use client";

import Link from "next/link";
import { AlertCircle, ArrowLeft, CheckCircle2, Copy, Download, Package, Play } from "lucide-react";
import type { Pipeline, ValidationResult } from "@/lib/api/types";
import { formatApiError } from "@/lib/api/client";
import type { AutoSaveLayoutStatus } from "./use-pipeline-save";
import { downloadPipelineExportWithAlert } from "@/lib/pipeline-export";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TriggerRunDialog } from "@/components/trigger-run-dialog";

interface DesignerToolbarProps {
  name: string;
  description: string;
  onNameChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  onValidate: () => void;
  onSave: () => void;
  validationResult: ValidationResult | null;
  isValidating: boolean;
  isSaving: boolean;
  saveLabel: string;
  /** Most recent server-side error from validate / save mutations.
   *  Surfaced inline so users see the reason instead of just the
   *  Next.js dev-mode unhandled-rejection overlay. */
  submitError?: unknown;
  layoutSaveStatus?: AutoSaveLayoutStatus;
  layoutSavedAt?: Date | null;
  layoutSaveError?: string | null;
  /** Set when editing an existing saved pipeline — enables the
   *  "Run", "Clone", and "Export JSON" buttons. None of those make
   *  sense before the pipeline has an id. */
  pipelineId?: string;
  pipelineVersion?: number;
  /** The persisted pipeline used by Export — needs ``name`` for the
   *  download filename slug, plus ``id`` for the API call. ``null``
   *  while the user is creating a new pipeline (no Export yet). */
  pipeline?: Pipeline | null;
}

export function DesignerToolbar({
  name,
  description,
  onNameChange,
  onDescriptionChange,
  onValidate,
  onSave,
  validationResult,
  isValidating,
  isSaving,
  saveLabel,
  submitError,
  layoutSaveStatus = "idle",
  layoutSavedAt,
  layoutSaveError,
  pipelineId,
  pipelineVersion,
  pipeline,
}: DesignerToolbarProps) {
  const canSave = !isSaving && name.length > 0 && validationResult?.valid !== false;
  const canRun = pipelineId != null && pipelineVersion != null;
  // Clone + Export only make sense for a saved pipeline. Both use
  // the persisted server id; pre-save there's nothing to clone or
  // export from.
  const canCloneOrExport = pipelineId != null && pipeline != null;

  return (
    <div className="border-b bg-background">
      <div className="flex items-center gap-2 p-3">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/pipelines" aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>

        <div className="flex-1 grid grid-cols-2 gap-2 max-w-3xl">
          <Field label="Name">
            <Input
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder="My pipeline"
              className="h-8"
            />
          </Field>
          <Field label="Description">
            <Input
              value={description}
              onChange={(e) => onDescriptionChange(e.target.value)}
              placeholder="Optional"
              className="h-8"
            />
          </Field>
        </div>

        <div className="flex items-center gap-2">
          <LayoutSaveStatus
            status={layoutSaveStatus}
            savedAt={layoutSavedAt}
            error={layoutSaveError}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onValidate}
            disabled={isValidating}
          >
            {isValidating ? "Validating…" : "Validate"}
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={onSave}
            disabled={!canSave}
          >
            {isSaving ? "Saving…" : saveLabel}
          </Button>
          {canCloneOrExport ? (
            <>
              <Button asChild type="button" variant="outline" size="sm">
                <Link href={`/pipelines/new?from=${pipelineId}`}>
                  <Copy className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
                  Clone
                </Link>
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => downloadPipelineExportWithAlert(pipeline)}
                title="Pipeline only — referenced agents must already exist on the target installation."
              >
                <Download className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
                Export
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => downloadPipelineExportWithAlert(pipeline, { bundle: true })}
                title="Pipeline + every referenced agent in one file — drop it on a target installation that doesn't have the agents yet."
              >
                <Package className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
                Export bundle
              </Button>
            </>
          ) : null}
          {canRun ? (
            <TriggerRunDialog
              pipelineId={pipelineId}
              pipelineName={name || "(unnamed)"}
              currentVersion={pipelineVersion}
            >
              {(open) => (
                <Button type="button" variant="secondary" size="sm" onClick={open}>
                  <Play className="h-3.5 w-3.5 mr-1" />
                  Run
                </Button>
              )}
            </TriggerRunDialog>
          ) : null}
        </div>
      </div>

      {submitError ? (
        <div
          className="px-3 py-2 border-t bg-destructive/10 text-xs flex items-start gap-1.5"
          role="alert"
        >
          <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0 mt-0.5" />
          <span className="text-destructive">{formatApiError(submitError)}</span>
        </div>
      ) : null}

      {validationResult && (
        <div
          className={`px-3 py-2 border-t text-xs space-y-1 ${
            validationResult.valid
              ? "bg-emerald-50 dark:bg-emerald-950/20"
              : "bg-destructive/10"
          }`}
        >
          <div className="flex items-center gap-1.5 font-medium">
            {validationResult.valid ? (
              <>
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                Pipeline is valid
                {validationResult.warnings.length > 0 &&
                  ` (${validationResult.warnings.length} warnings)`}
              </>
            ) : (
              <>
                <AlertCircle className="h-3.5 w-3.5 text-destructive" />
                {validationResult.errors.length} error
                {validationResult.errors.length === 1 ? "" : "s"}
                {validationResult.warnings.length > 0 &&
                  `, ${validationResult.warnings.length} warning${
                    validationResult.warnings.length === 1 ? "" : "s"
                  }`}
              </>
            )}
          </div>
          <ul className="space-y-0.5 ml-5">
            {validationResult.errors.map((err, i) => (
              <li key={`e-${i}`} className="text-destructive list-disc">
                {err}
              </li>
            ))}
            {validationResult.warnings.map((w, i) => (
              <li key={`w-${i}`} className="text-amber-700 list-disc">
                {w}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function LayoutSaveStatus({
  status,
  savedAt,
  error,
}: {
  status: AutoSaveLayoutStatus;
  savedAt?: Date | null;
  error?: string | null;
}) {
  if (status === "idle") return null;
  if (status === "saving") {
    return (
      <span className="text-xs text-muted-foreground" role="status" aria-live="polite">
        Saving layout...
      </span>
    );
  }
  if (status === "error") {
    return (
      <span
        className="text-xs text-destructive"
        title={error ?? undefined}
        role="status"
        aria-live="polite"
      >
        Layout save failed
      </span>
    );
  }
  return (
    <span className="text-xs text-muted-foreground" role="status" aria-live="polite">
      Saved {savedAt ? savedAt.toLocaleTimeString() : ""}
    </span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <Label className="text-[10px] text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}
