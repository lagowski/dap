"use client";

import { use } from "react";
import { LoadingState } from "@/components/ui/spinner";
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
    return <LoadingState className="p-6" />;
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
