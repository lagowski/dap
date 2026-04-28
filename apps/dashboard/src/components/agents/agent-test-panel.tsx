"use client";

import { useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, Play, XCircle } from "lucide-react";
import { useDryRunAgent } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { AgentDryRunDraft, AgentDryRunResponse } from "@/lib/api/types";

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
    // Best-effort defaults — keep types loose; the engine validates at
    // the runtime layer, the test panel just needs *something* renderable.
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

export function AgentTestPanel({ draft, draftBlockedReason }: AgentTestPanelProps) {
  const [contextText, setContextText] = useState(DEFAULT_CONTEXT);
  const dryRun = useDryRunAgent();

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

  const runTest = () => {
    if (!draft || !parseResult.ok) return;
    dryRun.mutate({ draft, context: parseResult.value });
  };

  const canRun = draft !== null && parseResult.ok && !dryRun.isPending;

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

      <div className="flex items-center gap-2">
        <Button type="button" onClick={runTest} disabled={!canRun}>
          <Play className="h-4 w-4 mr-1" />
          {dryRun.isPending ? "Running…" : "Run test"}
        </Button>
        {draftBlockedReason && draft === null ? (
          <span className="text-xs text-muted-foreground">
            {draftBlockedReason}
          </span>
        ) : null}
      </div>

      {dryRun.isError ? (
        <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          <div className="flex items-center gap-1.5 font-medium">
            <XCircle className="h-4 w-4" />
            Test run failed
          </div>
          <p className="mt-1 text-xs">{formatApiError(dryRun.error)}</p>
        </div>
      ) : null}

      {dryRun.data ? <DryRunResult result={dryRun.data} /> : null}
    </div>
  );
}

function DryRunResult({ result }: { result: AgentDryRunResponse }) {
  const { runtime_result: rr, output_schema_validation: osv } = result;

  return (
    <div className="space-y-4">
      <ResultStatusHeader runtimeSuccess={rr.success} validation={osv} />
      <RuntimeMetrics result={rr} />
      <OutputSchemaCheck validation={osv} />
      <CollapsibleBlock label="Rendered prompt (XML)" content={result.rendered_xml} />
      {rr.output ? <CollapsibleBlock label="stdout" content={rr.output} /> : null}
      {rr.errors.length > 0 ? (
        <CollapsibleBlock
          label="Runtime errors"
          content={rr.errors.join("\n\n")}
          tone="destructive"
        />
      ) : null}
      {rr.structured ? (
        <CollapsibleBlock
          label="Structured payload"
          content={JSON.stringify(rr.structured, null, 2)}
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
}: {
  label: string;
  content: string;
  tone?: "destructive";
}) {
  const [open, setOpen] = useState(true);
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
