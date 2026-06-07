"use client";

import { Suspense, useEffect, useId, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Copy } from "lucide-react";
import { useAgent, useCreateAgent } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AgentForm, type AgentFormValues } from "@/components/agents/agent-form";
import { AgentTabs, type AgentTab } from "@/components/agents/agent-tabs";
import {
  AgentTestPanel,
  type VariantBOverrides,
} from "@/components/agents/agent-test-panel";
import { TemplatePicker } from "@/components/agents/template-picker";
import { useAssistantPrefill } from "@/components/assistant/assistant-prefill";
import type { AgentTemplate } from "@/lib/agent-templates";
import type { Agent, AgentDryRunDraft } from "@/lib/api/types";

export default function NewAgentPage() {
  return (
    <Suspense fallback={<LoadingState className="p-6" />}>
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

  // Assistant prefill (#689 slice 3): consume once on mount. When present we
  // seed the form from it and skip the template chooser. Fully editable; the
  // user still has to click Create.
  const { consumePrefill } = useAssistantPrefill();
  const [prefillValues] = useState(() => consumePrefill("agent"));

  // Template picker state (#93). Only meaningful when not cloning —
  // cloning + template would be confusing UX. Picker stays hidden in
  // the clone flow.
  const [template, setTemplate] = useState<AgentTemplate | null>(null);
  const [tab, setTab] = useState<AgentTab>("form");
  const [snapshot, setSnapshot] = useState<{
    values: AgentFormValues;
    valid: boolean;
  } | null>(null);
  // Variant B overrides promoted from the Test panel (#105). When set,
  // bumped onto the form via ``promoteIteration`` in the form key so
  // AgentForm remounts with the diverged runtime/prompt.
  const [promoted, setPromoted] = useState<VariantBOverrides | null>(null);
  const [promoteIteration, setPromoteIteration] = useState(0);
  const formPanelId = useId();
  const testPanelId = useId();

  // Two-step flow: step 1 picks a template (or scratch), step 2 shows the
  // form with a "back to templates" affordance. Cloning skips straight to
  // the form. Replaces the old single-page picker-with-form-below.
  const [step, setStep] = useState<"choose" | "form">(
    prefillValues ? "form" : "choose",
  );
  const handlePick = (next: AgentTemplate | null) => {
    setTemplate(next);
    setStep("form");
  };
  const showChooser = !isCloning && !prefillValues && step === "choose";
  const showForm = isCloning || step === "form";

  // ``seedKey`` identifies which clone-source / template the form is
  // currently mounted against. We carry it alongside ``snapshot`` so
  // that switching templates / clone source clears stale snapshot
  // values from the previous seed (the existing "switch template to
  // reset" behaviour). Promote bumps within the same seed.
  const seedKey = prefillValues
    ? "assistant-prefill"
    : sourceQuery.data
      ? `clone:${sourceQuery.data.id}`
      : template !== null
        ? `template:${template.id}`
        : "scratch";

  // Drop snapshot + promoted overrides when the user switches between
  // clone source / template / scratch — those are explicit "reset to
  // this seed" actions and should *not* inherit edits from the previous
  // seed.
  useEffect(() => {
    setSnapshot(null);
    setPromoted(null);
    setPromoteIteration(0);
  }, [seedKey]);

  // initialValues precedence: promoted > snapshot (same seed) > clone
  // source > template > undefined. ``promoted`` overrides come from
  // the Test panel and need to win so the user actually sees the
  // promotion. ``snapshot`` is honoured only when its seed matches the
  // current one (effect above clears it on switch). Clone/template
  // are first-mount seeds.
  const baseInitialValues = prefillValues
    ? prefillInitialValuesFrom(prefillValues)
    : sourceQuery.data !== undefined
      ? cloneInitialValuesFrom(sourceQuery.data)
      : template !== null
        ? templateInitialValuesFrom(template)
        : undefined;
  const initialValues = applyPromoted(
    snapshot?.values ?? baseInitialValues,
    promoted,
  );

  const formKey = `${seedKey}:${promoteIteration}`;

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

      {showChooser ? (
        <Card>
          <CardContent className="pt-6">
            <TemplatePicker onSelect={handlePick} disabled={create.isPending} />
          </CardContent>
        </Card>
      ) : null}

      {showForm && !isCloning ? (
        <div className="flex items-center justify-between gap-2 rounded-md border bg-muted/30 px-3 py-2 text-sm">
          <button
            type="button"
            onClick={() => setStep("choose")}
            className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            Back to templates
          </button>
          <span className="text-muted-foreground">
            Starting from{" "}
            <span className="font-medium text-foreground">
              {template ? template.name : "scratch"}
            </span>
          </span>
        </div>
      ) : null}

      {showForm &&
      (!isCloning || (sourceQuery.data && sourceQuery.data.is_active)) ? (
        <AgentTabs
          tab={tab}
          onTabChange={setTab}
          formPanelId={formPanelId}
          testPanelId={testPanelId}
        />
      ) : null}

      {showForm ? (
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
                  onPromoteVariantB={(overrides) => {
                    setPromoted(overrides);
                    setPromoteIteration((n) => n + 1);
                    setTab("form");
                  }}
                />
              </div>
            </>
          )}
        </CardContent>
      </Card>
      ) : null}
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
 * Layer Variant B overrides (#105) on top of the form's initial values
 * so a Promote click actually changes what the next form mount shows.
 * Returns the input untouched when nothing was promoted yet.
 */
function applyPromoted(
  base: Partial<AgentFormValues> | undefined,
  promoted: VariantBOverrides | null,
): Partial<AgentFormValues> | undefined {
  if (!promoted) return base;
  return {
    ...(base ?? {}),
    runtime_id: promoted.runtime_id,
    runtime_config: promoted.runtime_config,
    prompt_template: promoted.prompt_template,
  };
}

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

/**
 * Map an assistant prefill payload (#689 slice 3) into the form's
 * ``initialValues`` shape, keeping only known fields with valid types — a
 * hallucinated/extra field can't reach the form. Everything stays editable.
 */
function prefillInitialValuesFrom(
  values: Record<string, unknown>,
): Partial<AgentFormValues> {
  const out: Partial<AgentFormValues> = {};
  if (typeof values.name === "string") out.name = values.name;
  if (typeof values.role === "string") out.role = values.role;
  if (typeof values.runtime_id === "string") out.runtime_id = values.runtime_id;
  if (values.runtime_config && typeof values.runtime_config === "object") {
    out.runtime_config = values.runtime_config as Record<string, unknown>;
  }
  if (typeof values.prompt_template === "string") {
    out.prompt_template = values.prompt_template;
  }
  if (Array.isArray(values.input_schema)) {
    out.input_schema = values.input_schema.filter(
      (x): x is string => typeof x === "string",
    );
  }
  if (Array.isArray(values.output_schema)) {
    out.output_schema = values.output_schema.filter(
      (x): x is string => typeof x === "string",
    );
  }
  return out;
}

/**
 * Map a static template into the form's ``initialValues`` shape.
 * The template's display name (e.g. "DeveloperJr — GLM via …")
 * collapses to its short prefix so the agent's saved name is
 * something the user actually wants to read in the agents list.
 */
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
