"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { PipelineDesigner } from "@/components/designer/designer";
import { PipelineTemplatePicker } from "@/components/designer/pipeline-template-picker";
import { Card, CardContent } from "@/components/ui/card";
import { useImportPipeline, usePipeline } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import type { PipelineTemplate } from "@/lib/pipeline-templates";

export default function NewPipelinePage() {
  return (
    <Suspense
      fallback={<div className="p-6 text-sm text-muted-foreground">Loading…</div>}
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
  const [pendingTemplateId, setPendingTemplateId] = useState<string | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);

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
      const created = await importPipeline.mutateAsync(template.bundle);
      router.push(`/pipelines/${created.id}/edit`);
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
  // explicitly asked to duplicate a specific pipeline, so the
  // template picker would just be noise.
  const showTemplatePicker = fromId === null;

  return (
    <div className="flex flex-col h-full">
      {showTemplatePicker ? (
        <div className="border-b bg-muted/20 p-4">
          {templateError ? (
            <p className="text-sm text-destructive mb-2" role="alert">
              Template import failed: {templateError}
            </p>
          ) : null}
          <PipelineTemplatePicker
            onUseTemplate={handleUseTemplate}
            // ``pendingTemplateId`` is the synchronous source of
            // truth — gating on the mutation's ``isPending`` alone
            // would leave a one-render-cycle hole where multiple
            // cards could be clicked before the mutation has
            // committed its state change.
            disabled={pendingTemplateId !== null}
            pendingTemplateId={pendingTemplateId}
          />
          <p className="mt-3 text-xs text-muted-foreground">
            Or scroll down to start from a blank canvas — the empty Designer
            below is the &quot;scratch&quot; path.
          </p>
        </div>
      ) : null}
      <div className="flex-1 min-h-0">
        <PipelineDesigner
          initialPipeline={null}
          seedFromPipeline={sourceQuery.data ?? null}
        />
      </div>
    </div>
  );
}
