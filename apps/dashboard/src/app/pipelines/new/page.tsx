"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { PipelineDesigner } from "@/components/designer/designer";
import { Card, CardContent } from "@/components/ui/card";
import { formatApiError } from "@/lib/api/client";
import { usePipeline } from "@/hooks/api";

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
  const searchParams = useSearchParams();
  // Treat empty / whitespace ``?from=`` as not-cloning so the hook
  // doesn't fire with an empty id.
  const fromIdRaw = searchParams.get("from");
  const fromId = fromIdRaw && fromIdRaw.trim() !== "" ? fromIdRaw.trim() : null;
  const sourceQuery = usePipeline(fromId);

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

  // Cloning path: pass the source as ``seedFromPipeline`` so the
  // Designer pre-fills nodes/edges/name without flipping into edit
  // mode. The save flow stays as create (no v2 of the source).
  return (
    <PipelineDesigner
      initialPipeline={null}
      seedFromPipeline={sourceQuery.data ?? null}
    />
  );
}
