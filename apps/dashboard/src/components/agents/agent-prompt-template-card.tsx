import { Card, CardContent } from "@/components/ui/card";

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
    const callablePath =
      typeof runtimeConfig?.callable_path === "string" ? runtimeConfig.callable_path : null;
    return (
      <Card>
        <CardContent className="pt-6 space-y-2">
          <h2 className="text-sm font-medium">Prompt template</h2>
          <p className="text-sm text-muted-foreground">
            Not used by this runtime. <span className="font-mono">python-func</span> agents run a
            Python callable
            {callablePath ? (
              <>
                {" "}
                (<span className="font-mono">{callablePath}</span>)
              </>
            ) : null}{" "}
            directly and never render a prompt template — the stored template is an inert,
            schema-required placeholder.
          </p>
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
