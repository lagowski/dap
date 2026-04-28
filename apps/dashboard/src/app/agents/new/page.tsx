"use client";

import { Suspense, useId, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Copy } from "lucide-react";
import { useAgent, useCreateAgent } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AgentForm, type AgentFormValues } from "@/components/agents/agent-form";
import { AgentTabs, type AgentTab } from "@/components/agents/agent-tabs";
import { AgentTestPanel } from "@/components/agents/agent-test-panel";
import { TemplatePicker } from "@/components/agents/template-picker";
import type { AgentTemplate } from "@/lib/agent-templates";
import type { Agent, AgentDryRunDraft } from "@/lib/api/types";

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

  // Template picker state (#93). Only meaningful when not cloning —
  // cloning + template would be confusing UX. Picker stays hidden in
  // the clone flow.
  const [template, setTemplate] = useState<AgentTemplate | null>(null);
  const [tab, setTab] = useState<AgentTab>("form");
  const [snapshot, setSnapshot] = useState<{
    values: AgentFormValues;
    valid: boolean;
  } | null>(null);
  const formPanelId = useId();
  const testPanelId = useId();

  // initialValues precedence: clone source > template > undefined.
  // The form is keyed so switching template force-remounts it,
  // letting react-hook-form pick up the new ``defaultValues``.
  const initialValues =
    sourceQuery.data !== undefined
      ? cloneInitialValuesFrom(sourceQuery.data)
      : template !== null
        ? templateInitialValuesFrom(template)
        : undefined;

  const formKey = sourceQuery.data
    ? `clone:${sourceQuery.data.id}`
    : template !== null
      ? `template:${template.id}`
      : "scratch";

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

      {!isCloning ? (
        <Card>
          <CardContent className="pt-6">
            <TemplatePicker
              value={template?.id ?? null}
              onChange={setTemplate}
            />
          </CardContent>
        </Card>
      ) : null}

      {!isCloning || (sourceQuery.data && sourceQuery.data.is_active) ? (
        <AgentTabs
          tab={tab}
          onTabChange={setTab}
          formPanelId={formPanelId}
          testPanelId={testPanelId}
        />
      ) : null}

      <Card>
        <CardContent className="pt-6">
          {isCloning && sourceQuery.isPending ? (
            <p className="text-sm text-muted-foreground">Loading source agent…</p>
          ) : isCloning && sourceQuery.isError ? (
            <SourceLoadError error={sourceQuery.error} />
          ) : isCloning && sourceQuery.data && !sourceQuery.data.is_active ? (
            <SourceArchivedError />
          ) : (
            <>
              <div
                id={formPanelId}
                role="tabpanel"
                hidden={tab !== "form"}
                className={tab === "form" ? "block" : "hidden"}
              >
                <AgentForm
                  key={formKey}
                  initialValues={initialValues}
                  onValuesChange={setSnapshot}
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
                  submitLabel={
                    isCloning
                      ? "Create cloned agent"
                      : template !== null
                        ? `Create from ${template.name.split(" — ")[0]}`
                        : "Create agent"
                  }
                />
              </div>
              <div
                id={testPanelId}
                role="tabpanel"
                hidden={tab !== "test"}
                className={tab === "test" ? "block" : "hidden"}
              >
                <AgentTestPanel
                  draft={
                    snapshot && snapshot.valid
                      ? toNewAgentDraft(snapshot.values, sourceQuery.data ?? null)
                      : null
                  }
                  draftBlockedReason={
                    snapshot === null
                      ? "Open the Form tab to fill in the agent first."
                      : !snapshot.valid
                        ? "Fix form errors before running a test."
                        : null
                  }
                />
              </div>
            </>
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

/**
 * Map a static template into the form's ``initialValues`` shape.
 * The template's display name (e.g. "DeveloperJr — GLM via …")
 * collapses to its short prefix so the agent's saved name is
 * something the user actually wants to read in the agents list.
 */
/**
 * Build the dry-run draft from live form values + the carry-through
 * fields (constraints, budget_limit_usd, timeout_ms) that the New form
 * doesn't surface. ``cloneSource`` supplies them when cloning; for a
 * scratch / template-based agent we fall back to ``AgentDryRunDraft``
 * defaults that match what AgentCreate accepts (empty constraints,
 * no budget cap on the agent itself — engine cap still applies, see
 * #103).
 */
function toNewAgentDraft(
  values: AgentFormValues,
  cloneSource: Agent | null,
): AgentDryRunDraft {
  return {
    name: values.name,
    role: values.role,
    runtime_id: values.runtime_id,
    runtime_config: values.runtime_config,
    prompt_template: values.prompt_template,
    input_schema: values.input_schema,
    output_schema: values.output_schema,
    constraints: cloneSource ? cloneSource.constraints : [],
    budget_limit_usd: cloneSource ? cloneSource.budget_limit_usd : null,
    timeout_ms: cloneSource ? cloneSource.timeout_ms : 60_000,
  };
}

function templateInitialValuesFrom(template: AgentTemplate) {
  // "DeveloperJr — GLM via OpenAI-compat" → "DeveloperJr"
  const displayPrefix = template.name.split(" — ")[0] ?? template.name;
  return {
    name: displayPrefix,
    role: template.role,
    runtime_id: template.runtime_id,
    runtime_config: template.runtime_config,
    prompt_template: template.prompt_template,
    input_schema: [...template.input_schema],
    output_schema: [...template.output_schema],
  };
}
