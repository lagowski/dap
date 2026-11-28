import { Card, CardContent } from "@/components/ui/card";

import { callablePathOf } from "@/lib/managed-agent";

import { PythonFuncPromptNote } from "./python-func-prompt-note";

interface PromptTemplateCardProps {
  runtimeId: string;
  promptTemplate: string;
  runtimeConfig: Record<string, unknown>;
  version: number;
}

/**
 * The agent detail "Prompt template" card (#736).
 *
 * For LLM/CLI runtimes it shows the stored template verbatim. For
 * ``python-func`` agents the template is never rendered — the agent runs a
 * Python callable directly and never calls an LLM — so the stored value is an
 * inert, schema-required placeholder. Showing it raw reads as a live prompt and
 * misleads (see #705/#724 for the equivalent fix on the run-node panel), so we
 * replace it with a short note that names the callable that actually runs.
 */
export function PromptTemplateCard({
  runtimeId,
  promptTemplate,
  runtimeConfig,
  version,
}: PromptTemplateCardProps) {
  if (runtimeId === "python-func") {
    const callablePath = callablePathOf({ runtime_id: runtimeId, runtime_config: runtimeConfig });
    return (
      <Card>
        <CardContent className="pt-6 space-y-2">
          <h2 className="text-sm font-medium">Prompt template</h2>
          <PythonFuncPromptNote callablePath={callablePath} />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="pt-6 space-y-2">
        <h2 className="text-sm font-medium">Prompt template (current — v{version})</h2>
        <pre className="rounded border bg-muted/30 p-3 text-xs overflow-x-auto whitespace-pre-wrap">
          {promptTemplate}
        </pre>
      </CardContent>
    </Card>
  );
}
