"use client";

/**
 * Compare-mode chrome (toggle, run-both controls, two-column layout)
 * plus the single-mode result wrapper. Extracted from the
 * ``AgentTestPanel`` orchestrator during the audit-D1 split.
 *
 * All three components here are pure presentational — no own state,
 * no business logic. The orchestrator wires them to the dry-run
 * mutations + Variant B state.
 */

import { ArrowLeftRight, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useDryRunAgent } from "@/hooks/api";
import type { AgentDryRunDraft } from "@/lib/api/types";

import { type VariantBOverrides } from "./diff-utils";
import { DryRunResult, ErrorBanner } from "./dry-run-result";
import { VariantASummary, VariantBEditor } from "./variant-b-editor";


export function SingleColumnResults({
  dryRun,
}: {
  dryRun: ReturnType<typeof useDryRunAgent>;
}) {
  return (
    <>
      {dryRun.isError ? <ErrorBanner error={dryRun.error} /> : null}
      {dryRun.data ? <DryRunResult result={dryRun.data} /> : null}
    </>
  );
}


export function CompareToggle({
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


export function CompareControls({
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
        <span className="text-xs text-muted-foreground">{blockedReason}</span>
      ) : null}
    </div>
  );
}


export function CompareColumns({
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
