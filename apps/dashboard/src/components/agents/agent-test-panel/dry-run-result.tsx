"use client";

/**
 * Result-rendering primitives extracted from ``AgentTestPanel``
 * (audit D1). Shared between single-mode (one column under "Run
 * test") and compare-mode (one block per Variant A / B column).
 *
 * Pure presentational — no state of its own beyond
 * ``CollapsibleBlock``'s open/closed flag. Run state, errors, and
 * compare-mode wiring live in the orchestrator file.
 */

import { useState } from "react";

import { AlertCircle, CheckCircle2, XCircle } from "lucide-react";

import { formatApiError } from "@/lib/api/client";
import type { AgentDryRunResponse } from "@/lib/api/types";

const COST_DECIMAL_PLACES = 4;
const MS_PER_SECOND = 1000;
const DURATION_DECIMAL_PLACES = 2;


export function ErrorBanner({ error }: { error: unknown }) {
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


export function DryRunResult({ result }: { result: AgentDryRunResponse }) {
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
