"use client";

import { use } from "react";
import { usePipeline } from "@/hooks/api";
import { PipelineDesigner } from "@/components/designer/designer";
import { Card, CardContent } from "@/components/ui/card";

export default function EditPipelinePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data, isPending, isError, error } = usePipeline(id);

  if (isPending) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }
  if (isError) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {(error as Error).message}
          </CardContent>
        </Card>
      </div>
    );
  }

  return <PipelineDesigner initialPipeline={data} />;
}
