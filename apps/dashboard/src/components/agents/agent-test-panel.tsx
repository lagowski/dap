"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowLeftRight,
  CheckCircle2,
  Play,
  XCircle,
} from "lucide-react";
import { useDryRunAgent } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { AgentDryRunDraft, AgentDryRunResponse } from "@/lib/api/types";
import { AGENT_RUNTIME_IDS } from "./runtime-config-schemas";

interface AgentTestPanelProps {
  /**
   * Current form values, sent verbatim as ``draft``. Two reasons we
   * never branch into the ``agent_id`` path even on a pristine Edit
   * form:
   *
   *  - For a pristine form the draft mirrors the persisted agent
   *    field-by-field (``initialValues`` come from the same Agent
   *    object), so the engine produces the identical RuntimeTask
   *    either way.
   *  - We'd otherwise need an "is dirty" check — react-hook-form's
   *    ``formState.isDirty`` doesn't see the runtime_config /
   *    input_schema / output_schema state we manage outside the
   *    Zod schema, and a partial dirty check is worse than always
   *    sending the full snapshot.
   *
   * The payload size cost is negligible for one agent (~few KB).
   */
  draft: AgentDryRunDraft | null;
  /**
   * Reason ``draft`` is null. Surfaced so the user understands why
   * "Run test" is disabled (e.g. "fill in name + prompt template").
   */
  draftBlockedReason?: string | null;
  /**
   * Push diverged Variant B fields back into the parent form (#105).
   * The parent typically rebuilds ``initialValues`` and bumps the
   * form's ``key`` so AgentForm remounts with the promoted values.
   * Omit to hide the "Promote" button.
   */
  onPromoteVariantB?: (overrides: VariantBOverrides) => void;
}

/**
 * Subset of fields Variant B can diverge on. Other agent fields
 * (name, role, input/output schema, constraints, budget, timeout)
 * stay shared with Variant A — those rarely vary in an A/B test
 * and exposing them would dilute the panel.
 */
export interface VariantBOverrides {
  runtime_id: string;
  runtime_config: Record<string, unknown>;
  prompt_template: string;
}

const DEFAULT_CONTEXT = "{}";

/**
 * Slug a raw input_schema sample seed: produces a JSON object whose
 * keys match the agent's declared inputs with placeholder values, so
 * the user has a starting point instead of an empty ``{}``.
 */
function sampleContextFromInputs(inputs: string[]): string {
  if (inputs.length === 0) return DEFAULT_CONTEXT;
  const seed: Record<string, unknown> = {};
  for (const field of inputs) {
    if (field.endsWith("_ids") || field.endsWith("_files")) {
      seed[field] = [];
    } else if (field.startsWith("tests_") || field.startsWith("is_")) {
      seed[field] = false;
    } else if (field === "available_issues") {
      seed[field] = [{ id: 1, title: "Sample issue" }];
    } else if (field === "max_attempts" || field === "attempt") {
      seed[field] = 1;
    } else {
      seed[field] = "";
    }
  }
  return JSON.stringify(seed, null, 2);
}

export function AgentTestPanel({
  draft,
  draftBlockedReason,
  onPromoteVariantB,
}: AgentTestPanelProps) {
  const [contextText, setContextText] = useState(DEFAULT_CONTEXT);
  const [compareOn, setCompareOn] = useState(false);
  const [variantB, setVariantB] = useState<VariantBOverrides | null>(null);
  // Set the moment a VariantBEditor change handler runs (not on the
  // initial mirror-from-draft). Lets the mirror effect keep B in sync
  // with A while the user hasn't actually diverged yet — addressing
  // the original "``variantB !== null`` flips true after first mirror"
  // bug where edits to A wouldn't propagate.
  const [variantBEdited, setVariantBEdited] = useState(false);
  // VariantBEditor reports its runtime_config JSON parse status here
  // so "Run both" can be gated on it — without this, an invalid
  // textarea would silently run with the last-known-good value.
  const [variantBJsonValid, setVariantBJsonValid] = useState(true);

  const dryRunA = useDryRunAgent();
  const dryRunB = useDryRunAgent();

  // Initialise / refresh Variant B from the current draft when compare
  // is toggled on, or when the form changes shape while Variant B
  // hasn't been edited yet. Once the user edits Variant B we keep
  // their overrides — that's the whole point of compare mode.
  useEffect(() => {
    if (!compareOn) return;
    if (draft === null) return;
    if (variantBEdited) return;
    setVariantB({
      runtime_id: draft.runtime_id,
      runtime_config: draft.runtime_config,
      prompt_template: draft.prompt_template,
    });
  }, [compareOn, draft, variantBEdited]);

  const parseResult = useMemo<
    | { ok: true; value: Record<string, unknown> }
    | { ok: false; error: string }
  >(() => {
    const trimmed = contextText.trim();
    if (trimmed === "") return { ok: true, value: {} };
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (
        parsed === null ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        return { ok: false, error: "Context must be a JSON object" };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }, [contextText]);

  const seedFromInputs = () => {
    if (!draft) return;
    setContextText(sampleContextFromInputs(draft.input_schema));
  };

  const runA = () => {
    if (!draft || !parseResult.ok) return;
    dryRunA.mutate({ draft, context: parseResult.value });
  };

  const runBoth = () => {
    if (!draft || !parseResult.ok || !variantB) return;
    const draftB: AgentDryRunDraft = {
      ...draft,
      runtime_id: variantB.runtime_id,
      runtime_config: variantB.runtime_config,
      prompt_template: variantB.prompt_template,
    };
    dryRunA.mutate({ draft, context: parseResult.value });
    dryRunB.mutate({ draft: draftB, context: parseResult.value });
  };

  const canRunSingle = draft !== null && parseResult.ok && !dryRunA.isPending;
  const canRunBoth =
    canRunSingle &&
    variantB !== null &&
    !dryRunB.isPending &&
    variantBJsonValid;

  const diffs = variantB && draft ? diffVariantB(draft, variantB) : null;
  const hasDivergence = diffs ? diffs.length > 0 : false;

  const handlePromote = () => {
    if (!variantB || !onPromoteVariantB) return;
    onPromoteVariantB(variantB);
  };

  return (
    <div className="space-y-4">
      <div className="text-sm text-muted-foreground space-y-1">
        <p>
          Runs the agent end-to-end against sample context.{" "}
          <span className="font-medium">
            Costs real tokens up to ~$0.50 per call
          </span>{" "}
          (engine cap; lower it via{" "}
          <code className="font-mono text-xs">DAP_DRY_RUN_BUDGET_USD</code>).
          CLI runtimes (claude-code, aider, codex, bash) edit files in a temp
          directory that&apos;s discarded after the test — no impact on your
          working tree.
        </p>
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium">Sample context (JSON)</label>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={seedFromInputs}
            disabled={draft === null || draft.input_schema.length === 0}
            title={
              draft === null
                ? "Fill in the form first"
                : draft.input_schema.length === 0
                  ? "No inputs declared on the agent"
                  : "Pre-fill with placeholders for declared inputs"
            }
          >
            Use sample state
          </Button>
        </div>
        <Textarea
          value={contextText}
          onChange={(e) => setContextText(e.target.value)}
          rows={6}
          className="font-mono text-xs"
          placeholder='{ "available_issues": [...] }'
        />
        {!parseResult.ok ? (
          <p className="text-xs text-destructive" role="alert">
            JSON parse error: {parseResult.error}
          </p>
        ) : null}
      </div>

      <CompareToggle
        on={compareOn}
        onChange={(next) => {
          setCompareOn(next);
          if (!next) {
            // Discard variant B + its result when compare is turned off
            // so re-toggling later starts from the form's current state
            // rather than stale overrides.
            setVariantB(null);
            setVariantBEdited(false);
            setVariantBJsonValid(true);
            dryRunB.reset();
          }
        }}
      />

      {compareOn ? (
        <CompareControls
          canRunBoth={canRunBoth}
          isPending={dryRunA.isPending || dryRunB.isPending}
          onRunBoth={runBoth}
          hasDivergence={hasDivergence}
          showPromote={onPromoteVariantB !== undefined}
          onPromote={handlePromote}
          blockedReason={
            !variantBJsonValid
              ? "Variant B's runtime_config JSON is invalid — fix it to enable Run both."
              : draftBlockedReason && draft === null
                ? draftBlockedReason
                : null
          }
        />
      ) : (
        <div className="flex items-center gap-2">
          <Button type="button" onClick={runA} disabled={!canRunSingle}>
            <Play className="h-4 w-4 mr-1" />
            {dryRunA.isPending ? "Running…" : "Run test"}
          </Button>
          {draftBlockedReason && draft === null ? (
            <span className="text-xs text-muted-foreground">
              {draftBlockedReason}
            </span>
          ) : null}
        </div>
      )}

      {!compareOn ? (
        <SingleColumnResults dryRun={dryRunA} />
      ) : (
        <CompareColumns
          variantA={draft}
          variantB={variantB}
          onVariantBChange={(next) => {
            setVariantB(next);
            setVariantBEdited(true);
          }}
          onVariantBJsonValidityChange={setVariantBJsonValid}
          dryRunA={dryRunA}
          dryRunB={dryRunB}
          diffs={new Set(diffs ?? [])}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single-column (compare off) results
// ---------------------------------------------------------------------------

function SingleColumnResults({
  dryRun,
}: {
  dryRun: ReturnType<typeof useDryRunAgent>;
}) {
  return (
    <>
      {dryRun.isError ? (
        <ErrorBanner error={dryRun.error} />
      ) : null}
      {dryRun.data ? <DryRunResult result={dryRun.data} /> : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// Compare mode — toggle, controls, two columns
// ---------------------------------------------------------------------------

function CompareToggle({
  on,
  onChange,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
      <input
        type="checkbox"
        checked={on}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4"
      />
      <ArrowLeftRight className="h-3.5 w-3.5" aria-hidden="true" />
      <span className="font-medium">Compare with Variant B</span>
      <span className="text-xs text-muted-foreground">
        (run two configs in parallel — costs 2× the per-call budget)
      </span>
    </label>
  );
}

function CompareControls({
  canRunBoth,
  isPending,
  onRunBoth,
  hasDivergence,
  showPromote,
  onPromote,
  blockedReason,
}: {
  canRunBoth: boolean;
  isPending: boolean;
  onRunBoth: () => void;
  hasDivergence: boolean;
  showPromote: boolean;
  onPromote: () => void;
  blockedReason: string | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button type="button" onClick={onRunBoth} disabled={!canRunBoth}>
        <Play className="h-4 w-4 mr-1" />
        {isPending ? "Running both…" : "Run both"}
      </Button>
      {showPromote ? (
        <Button
          type="button"
          variant="outline"
          onClick={onPromote}
          disabled={!hasDivergence}
          title={
            hasDivergence
              ? "Copy Variant B's runtime / prompt back into the main form"
              : "Variant B matches Variant A — nothing to promote"
          }
        >
          Promote B → form
        </Button>
      ) : null}
      {blockedReason ? (
        <span className="text-xs text-muted-foreground">
          {blockedReason}
        </span>
      ) : null}
    </div>
  );
}

function CompareColumns({
  variantA,
  variantB,
  onVariantBChange,
  onVariantBJsonValidityChange,
  dryRunA,
  dryRunB,
  diffs,
}: {
  variantA: AgentDryRunDraft | null;
  variantB: VariantBOverrides | null;
  onVariantBChange: (next: VariantBOverrides) => void;
  onVariantBJsonValidityChange: (valid: boolean) => void;
  dryRunA: ReturnType<typeof useDryRunAgent>;
  dryRunB: ReturnType<typeof useDryRunAgent>;
  diffs: Set<keyof VariantBOverrides>;
}) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <div className="space-y-3">
        <ColumnHeader label="Variant A" subtitle="from Form tab" />
        <VariantASummary draft={variantA} />
        {dryRunA.isError ? <ErrorBanner error={dryRunA.error} /> : null}
        {dryRunA.data ? <DryRunResult result={dryRunA.data} /> : null}
      </div>
      <div className="space-y-3">
        <ColumnHeader
          label="Variant B"
          subtitle={diffs.size > 0 ? `${diffs.size} field(s) diverged` : "matches A"}
        />
        <VariantBEditor
          value={variantB}
          onChange={onVariantBChange}
          onJsonValidityChange={onVariantBJsonValidityChange}
          diffs={diffs}
        />
        {dryRunB.isError ? <ErrorBanner error={dryRunB.error} /> : null}
        {dryRunB.data ? <DryRunResult result={dryRunB.data} /> : null}
      </div>
    </div>
  );
}

function ColumnHeader({ label, subtitle }: { label: string; subtitle: string }) {
  return (
    <div className="border-b pb-1.5">
      <div className="text-sm font-medium">{label}</div>
      <div className="text-xs text-muted-foreground">{subtitle}</div>
    </div>
  );
}

function VariantASummary({ draft }: { draft: AgentDryRunDraft | null }) {
  if (!draft) {
    return (
      <p className="text-xs text-muted-foreground">
        Fill in the form to populate Variant A.
      </p>
    );
  }
  const modelId =
    typeof draft.runtime_config["model_id"] === "string"
      ? (draft.runtime_config["model_id"] as string)
      : null;
  return (
    <div className="rounded border bg-muted/30 p-2 space-y-1.5 text-xs">
      <SummaryRow label="runtime_id" value={draft.runtime_id} />
      {modelId ? <SummaryRow label="model_id" value={modelId} /> : null}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          prompt_template (excerpt)
        </summary>
        <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px]">
          {draft.prompt_template.length > VARIANT_A_PROMPT_LIMIT
            ? `${draft.prompt_template.slice(0, VARIANT_A_PROMPT_LIMIT)}…`
            : draft.prompt_template}
        </pre>
      </details>
    </div>
  );
}

const VARIANT_A_PROMPT_LIMIT = 600;

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="font-mono">{value}</span>
    </div>
  );
}

function VariantBEditor({
  value,
  onChange,
  onJsonValidityChange,
  diffs,
}: {
  value: VariantBOverrides | null;
  onChange: (next: VariantBOverrides) => void;
  onJsonValidityChange: (valid: boolean) => void;
  diffs: Set<keyof VariantBOverrides>;
}) {
  // Hooks before any conditional return — rules-of-hooks. The empty
  // string + ``null`` guards inside the body keep the editor inert when
  // ``value`` is null (compare just toggled on with an invalid form).
  const initialJson = value ? JSON.stringify(value.runtime_config, null, 2) : "";
  const [runtimeConfigText, setRuntimeConfigText] = useState(initialJson);
  const [runtimeConfigError, setRuntimeConfigError] = useState<string | null>(
    null,
  );

  // Re-sync the textarea when the parent rebuilds Variant B (e.g. after
  // compare toggle re-init). We don't want every keystroke to trigger
  // this — guard on a structural mismatch using ``stableStringify`` so
  // a re-ordered-but-identical config doesn't clobber the user's text.
  useEffect(() => {
    if (!value) return;
    const expected = JSON.stringify(value.runtime_config, null, 2);
    if (expected === runtimeConfigText) return;
    try {
      const parsed: unknown = JSON.parse(runtimeConfigText);
      if (
        stableStringify(parsed) !== stableStringify(value.runtime_config)
      ) {
        setRuntimeConfigText(expected);
      }
    } catch {
      setRuntimeConfigText(expected);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value?.runtime_config]);

  // Mirror the JSON parse status up to the parent so "Run both" can
  // gate on it. ``onJsonValidityChange`` runs on every flip rather
  // than every keystroke so we don't churn the parent unnecessarily.
  const wasValid = runtimeConfigError === null;
  useEffect(() => {
    onJsonValidityChange(wasValid);
  }, [wasValid, onJsonValidityChange]);

  if (!value) {
    return (
      <p className="text-xs text-muted-foreground">
        Variant B will mirror Variant A once you toggle compare on with a
        valid form.
      </p>
    );
  }

  const updateRuntimeConfig = (text: string) => {
    setRuntimeConfigText(text);
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        setRuntimeConfigError("runtime_config must be a JSON object");
        return;
      }
      setRuntimeConfigError(null);
      onChange({ ...value, runtime_config: parsed as Record<string, unknown> });
    } catch (err) {
      setRuntimeConfigError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="rounded border bg-muted/10 p-2 space-y-2">
      <FieldLabel
        label="runtime_id"
        diverged={diffs.has("runtime_id")}
      >
        <select
          value={value.runtime_id}
          onChange={(e) => onChange({ ...value, runtime_id: e.target.value })}
          className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
        >
          {AGENT_RUNTIME_IDS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </FieldLabel>

      <FieldLabel
        label="runtime_config (JSON)"
        diverged={diffs.has("runtime_config")}
      >
        <Textarea
          value={runtimeConfigText}
          onChange={(e) => updateRuntimeConfig(e.target.value)}
          rows={6}
          className="font-mono text-[11px]"
        />
        {runtimeConfigError ? (
          <p className="text-xs text-destructive">{runtimeConfigError}</p>
        ) : null}
      </FieldLabel>

      <FieldLabel
        label="prompt_template"
        diverged={diffs.has("prompt_template")}
      >
        <Textarea
          value={value.prompt_template}
          onChange={(e) =>
            onChange({ ...value, prompt_template: e.target.value })
          }
          rows={6}
          className="font-mono text-[11px]"
        />
      </FieldLabel>
    </div>
  );
}

function FieldLabel({
  label,
  diverged,
  children,
}: {
  label: string;
  diverged: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        {diverged ? (
          <span className="rounded bg-amber-500/15 px-1 text-[10px] font-medium uppercase text-amber-700 dark:text-amber-500">
            changed
          </span>
        ) : null}
      </div>
      {children}
    </div>
  );
}

/**
 * Order-insensitive deep equality on ``runtime_config`` — plain
 * ``JSON.stringify`` would mark ``{a:1,b:2}`` as different from
 * ``{b:2,a:1}`` even though the configs are identical, which would
 * then enable the Promote button on a no-op diff. We sort keys
 * recursively so the comparison is structural.
 */
function sortKeysRecursively(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortKeysRecursively);
  }
  if (value && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((acc, key) => {
        acc[key] = sortKeysRecursively(
          (value as Record<string, unknown>)[key],
        );
        return acc;
      }, {});
  }
  return value;
}

function stableStringify(value: unknown): string {
  return JSON.stringify(sortKeysRecursively(value));
}

function diffVariantB(
  a: AgentDryRunDraft,
  b: VariantBOverrides,
): (keyof VariantBOverrides)[] {
  const out: (keyof VariantBOverrides)[] = [];
  if (a.runtime_id !== b.runtime_id) out.push("runtime_id");
  if (stableStringify(a.runtime_config) !== stableStringify(b.runtime_config)) {
    out.push("runtime_config");
  }
  if (a.prompt_template !== b.prompt_template) out.push("prompt_template");
  return out;
}

// ---------------------------------------------------------------------------
// Result rendering — shared between single and compare modes
// ---------------------------------------------------------------------------

function ErrorBanner({ error }: { error: unknown }) {
  return (
    <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
      <div className="flex items-center gap-1.5 font-medium">
        <XCircle className="h-4 w-4" />
        Test run failed
      </div>
      <p className="mt-1 text-xs">{formatApiError(error)}</p>
    </div>
  );
}

function DryRunResult({ result }: { result: AgentDryRunResponse }) {
  const { runtime_result: rr, output_schema_validation: osv } = result;

  return (
    <div className="space-y-3">
      <ResultStatusHeader runtimeSuccess={rr.success} validation={osv} />
      <RuntimeMetrics result={rr} />
      <OutputSchemaCheck validation={osv} />
      <CollapsibleBlock
        label="Rendered prompt (XML)"
        content={result.rendered_xml}
        defaultOpen={false}
      />
      {rr.output ? (
        <CollapsibleBlock label="stdout" content={rr.output} defaultOpen />
      ) : null}
      {rr.errors.length > 0 ? (
        <CollapsibleBlock
          label="Runtime errors"
          content={rr.errors.join("\n\n")}
          tone="destructive"
          defaultOpen
        />
      ) : null}
      {rr.structured ? (
        <CollapsibleBlock
          label="Structured payload"
          content={JSON.stringify(rr.structured, null, 2)}
          defaultOpen={false}
        />
      ) : null}
    </div>
  );
}

function ResultStatusHeader({
  runtimeSuccess,
  validation,
}: {
  runtimeSuccess: boolean;
  validation: { valid: boolean; checked: boolean };
}) {
  if (!runtimeSuccess) {
    return (
      <div className="flex items-center gap-1.5 text-sm font-medium text-destructive">
        <XCircle className="h-4 w-4" />
        Runtime returned an error
      </div>
    );
  }
  if (validation.checked && !validation.valid) {
    return (
      <div className="flex items-center gap-1.5 text-sm font-medium text-amber-600">
        <AlertCircle className="h-4 w-4" />
        Test ran, but output schema check failed
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1.5 text-sm font-medium text-green-600">
      <CheckCircle2 className="h-4 w-4" />
      Test ran successfully
    </div>
  );
}

const COST_DECIMAL_PLACES = 4;
const MS_PER_SECOND = 1000;
const DURATION_DECIMAL_PLACES = 2;

function RuntimeMetrics({
  result,
}: {
  result: { tokens_used: number | null; cost_usd: number | null; duration_ms: number };
}) {
  const tokens = result.tokens_used ?? 0;
  const cost = result.cost_usd ?? 0;
  const seconds = result.duration_ms / MS_PER_SECOND;
  return (
    <div className="grid grid-cols-3 gap-2 text-xs">
      <Metric label="tokens" value={tokens.toLocaleString()} />
      <Metric label="cost" value={`$${cost.toFixed(COST_DECIMAL_PLACES)}`} />
      <Metric
        label="duration"
        value={`${seconds.toFixed(DURATION_DECIMAL_PLACES)}s`}
      />
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border bg-muted/30 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="font-mono">{value}</div>
    </div>
  );
}

function OutputSchemaCheck({
  validation,
}: {
  validation: {
    valid: boolean;
    checked: boolean;
    missing_fields: string[];
    extra_fields: string[];
    note?: string | null;
  };
}) {
  if (!validation.checked) {
    return validation.note ? (
      <p className="text-xs text-muted-foreground">
        Output schema not checked: {validation.note}
      </p>
    ) : null;
  }
  if (validation.valid) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-green-600">
        <CheckCircle2 className="h-3.5 w-3.5" />
        Output schema satisfied
      </div>
    );
  }
  return (
    <div className="rounded border border-amber-500/40 bg-amber-500/5 p-2 text-xs space-y-1">
      <div className="flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-500">
        <AlertCircle className="h-3.5 w-3.5" />
        Output schema mismatch
      </div>
      {validation.missing_fields.length > 0 ? (
        <p>
          Missing fields:{" "}
          <span className="font-mono">
            {validation.missing_fields.join(", ")}
          </span>
        </p>
      ) : null}
      {validation.extra_fields.length > 0 ? (
        <p>
          Extra fields (not declared in output_schema):{" "}
          <span className="font-mono">{validation.extra_fields.join(", ")}</span>
        </p>
      ) : null}
    </div>
  );
}

function CollapsibleBlock({
  label,
  content,
  tone,
  defaultOpen = true,
}: {
  label: string;
  content: string;
  tone?: "destructive";
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const toneClass =
    tone === "destructive"
      ? "border-destructive/40 bg-destructive/5"
      : "border-border bg-muted/30";
  return (
    <details
      open={open}
      onToggle={(e: React.SyntheticEvent<HTMLDetailsElement>) =>
        setOpen(e.currentTarget.open)
      }
      className={`rounded border ${toneClass}`}
    >
      <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </summary>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words px-3 pb-3 font-mono text-xs">
        {content}
      </pre>
    </details>
  );
}
