"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Copy } from "lucide-react";
import { useAgent, useCreateAgent } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AgentForm } from "@/components/agents/agent-form";

export default function NewAgentPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading…</div>}>
      <NewAgentPageContent />
    </Suspense>
  );
}

function NewAgentPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Treat an empty / whitespace-only ``?from=`` as not-cloning. Without
  // this, ``/agents/new?from=`` would trip the cloning branch and call
  // ``useAgent("")``, which the hook rejects with a noisy error.
  const fromIdRaw = searchParams.get("from");
  const fromId = fromIdRaw && fromIdRaw.trim() !== "" ? fromIdRaw.trim() : null;
  const create = useCreateAgent();

  const isCloning = fromId !== null;
  // The hook is enabled only when fromId is present; in the
  // freshly-create path it returns isPending=false / data=undefined
  // and we render the empty form straight away.
  const sourceQuery = useAgent(fromId);

  return (
    <div className="p-6 space-y-4 max-w-3xl">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/agents" aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold">
          {isCloning ? "Clone agent" : "New agent"}
        </h1>
        {isCloning && sourceQuery.data ? (
          <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
            from {sourceQuery.data.name}
          </span>
        ) : null}
      </div>

      <Card>
        <CardContent className="pt-6">
          {isCloning && sourceQuery.isPending ? (
            <p className="text-sm text-muted-foreground">Loading source agent…</p>
          ) : isCloning && sourceQuery.isError ? (
            <SourceLoadError error={sourceQuery.error} />
          ) : isCloning && sourceQuery.data && !sourceQuery.data.is_active ? (
            <SourceArchivedError />
          ) : (
            <AgentForm
              initialValues={
                sourceQuery.data
                  ? cloneInitialValuesFrom(sourceQuery.data)
                  : undefined
              }
              onSubmit={async (values) => {
                await create.mutateAsync({
                  name: values.name,
                  role: values.role,
                  runtime_id: values.runtime_id,
                  runtime_config: values.runtime_config,
                  prompt_template: values.prompt_template,
                  input_schema: values.input_schema,
                  output_schema: values.output_schema,
                  // Carry through the fields the form doesn't surface
                  // when cloning so the duplicate matches the source's
                  // full config (matches user intent of "duplicate me").
                  ...(sourceQuery.data
                    ? {
                        constraints: sourceQuery.data.constraints,
                        budget_limit_usd: sourceQuery.data.budget_limit_usd,
                        timeout_ms: sourceQuery.data.timeout_ms,
                      }
                    : {}),
                });
                router.push("/agents");
              }}
              isPending={create.isPending}
              submitError={create.error}
              submitLabel={isCloning ? "Create cloned agent" : "Create agent"}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SourceLoadError({ error }: { error: unknown }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-destructive" role="alert">
        Could not load the source agent: {formatApiError(error)}
      </p>
      <Button asChild variant="outline" size="sm">
        <Link href="/agents/new">Start from scratch instead</Link>
      </Button>
    </div>
  );
}

function SourceArchivedError() {
  return (
    <div className="space-y-2">
      <p className="text-sm text-destructive" role="alert">
        The source agent is archived — clone unavailable.
      </p>
      <Button asChild variant="outline" size="sm">
        <Link href="/agents/new">Start from scratch instead</Link>
      </Button>
    </div>
  );
}

/**
 * Map a server ``Agent`` to the form's ``initialValues`` shape, with
 * the name suffixed so a clone is saveable without renaming.
 */
function cloneInitialValuesFrom(source: {
  name: string;
  role: string;
  runtime_id: string;
  runtime_config: Record<string, unknown>;
  prompt_template: string;
  input_schema: string[];
  output_schema: string[];
}) {
  return {
    name: `${source.name} (copy)`,
    role: source.role,
    runtime_id: source.runtime_id,
    runtime_config: source.runtime_config,
    prompt_template: source.prompt_template,
    input_schema: source.input_schema,
    output_schema: source.output_schema,
  };
}
