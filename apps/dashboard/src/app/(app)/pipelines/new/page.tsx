"use client";

import { Suspense, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PipelineDesigner } from "@/components/designer/designer";
import { PipelineTemplatePicker } from "@/components/designer/pipeline-template-picker";
import { BackendProfileImportDialog } from "@/components/pipelines/backend-profile-import-dialog";
import { Card, CardContent } from "@/components/ui/card";
import {
  useImportPipeline,
  useInspectPipelineImportBackends,
  usePipeline,
} from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import type {
  BackendProfilesInspectionResponse,
  PipelineExport,
} from "@/lib/api/types";
import {
  bundleHasBackendProfiles,
  hasInspectableProfiles,
} from "@/lib/backend-profile-import";
import type { PipelineTemplate } from "@/lib/pipeline-templates";

interface BackendProfileDialogState {
  bundle: PipelineExport;
  inspection: BackendProfilesInspectionResponse;
}

export default function NewPipelinePage() {
  return (
    <Suspense
      fallback={<LoadingState className="p-6" />}
    >
      <NewPipelinePageContent />
    </Suspense>
  );
}

function NewPipelinePageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Treat empty / whitespace ``?from=`` as not-cloning so the hook
  // doesn't fire with an empty id.
  const fromIdRaw = searchParams.get("from");
  const fromId = fromIdRaw && fromIdRaw.trim() !== "" ? fromIdRaw.trim() : null;
  const sourceQuery = usePipeline(fromId);
  const importPipeline = useImportPipeline();
  const inspectBackends = useInspectPipelineImportBackends();
  const [pendingTemplateId, setPendingTemplateId] = useState<string | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [backendDialog, setBackendDialog] =
    useState<BackendProfileDialogState | null>(null);
  // Two-step flow: step 1 picks a template (or blank canvas), step 2 shows
  // the Designer with a "back to templates" affordance. Cloning skips to
  // the Designer. (Picking a template navigates away to the edit page.)
  const [step, setStep] = useState<"choose" | "design">("choose");

  const importBundle = async (bundle: PipelineExport) => {
    const created = await importPipeline.mutateAsync(bundle);
    router.push(`/pipelines/${created.id}/edit`);
  };

  const handleUseTemplate = async (template: PipelineTemplate) => {
    // Guard at the top of the handler: ``importPipeline.isPending``
    // doesn't flip true until React commits the mutation's state
    // change, so a fast double-click could fire two ``mutateAsync``
    // calls before the disabled-state propagates. ``pendingTemplateId``
    // is set synchronously below, so reading it here is the source
    // of truth.
    if (pendingTemplateId !== null) return;
    setTemplateError(null);
    setPendingTemplateId(template.id);
    try {
      if (bundleHasBackendProfiles(template.bundle)) {
        const inspection = await inspectBackends.mutateAsync(template.bundle);
        if (hasInspectableProfiles(inspection)) {
          setBackendDialog({ bundle: template.bundle, inspection });
          return;
        }
      }
      await importBundle(template.bundle);
    } catch (err) {
      setTemplateError(formatApiError(err));
    } finally {
      // Clear in ``finally`` so the picker stays usable after an
      // error, and so the state is consistent even if the user
      // cancels mid-navigation. On success the page unmounts and
      // the setter is a no-op.
      setPendingTemplateId(null);
    }
  };

  const handleConfiguredImport = async (bundle: PipelineExport) => {
    setTemplateError(null);
    try {
      await importBundle(bundle);
      setBackendDialog(null);
    } catch (err) {
      setTemplateError(formatApiError(err));
    }
  };

  if (fromId !== null && sourceQuery.isPending) {
    return <div className="p-6 text-sm text-muted-foreground">Loading source pipeline…</div>;
  }
  if (fromId !== null && sourceQuery.isError) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            Could not load source pipeline: {formatApiError(sourceQuery.error)}
          </CardContent>
        </Card>
      </div>
    );
  }

  // Clone flow takes precedence — when ``?from=...`` is set the user
  // explicitly asked to duplicate a specific pipeline, so the chooser
  // would just be noise; jump straight to the seeded Designer.
  const showChooser = fromId === null && step === "choose";
  const showDesigner = fromId !== null || step === "design";

  return (
    <div className="flex flex-col h-full">
      {showChooser ? (
        <div className="flex-1 min-h-0 w-full overflow-y-auto p-6">
          <div className="mx-auto max-w-3xl">
            {templateError ? (
              <p className="text-sm text-destructive mb-3" role="alert">
                Template import failed: {templateError}
              </p>
            ) : null}
            <PipelineTemplatePicker
              onUseTemplate={handleUseTemplate}
              onStartScratch={() => setStep("design")}
              // ``pendingTemplateId`` is the synchronous source of truth —
              // gating on the mutation's ``isPending`` alone would leave a
              // one-render-cycle hole where multiple rows could be clicked
              // before the mutation has committed its state change.
              disabled={pendingTemplateId !== null || backendDialog !== null}
              pendingTemplateId={pendingTemplateId}
            />
          </div>
        </div>
      ) : null}

      {showDesigner ? (
        <>
          {fromId === null ? (
            <div className="border-b bg-muted/20 px-4 py-2">
              <button
                type="button"
                onClick={() => setStep("choose")}
                className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                Back to templates
              </button>
            </div>
          ) : null}
          <div className="flex-1 min-h-0">
            <PipelineDesigner
              initialPipeline={null}
              seedFromPipeline={sourceQuery.data ?? null}
            />
          </div>
        </>
      ) : null}

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
