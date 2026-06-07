"use client";

/**
 * Agent Test Panel orchestrator (audit D1).
 *
 * After the audit-D1 split this file owns ONLY the top-level component
 * and its state wiring. Presentational pieces live under
 * ``components/agents/agent-test-panel/``:
 *
 * - ``diff-utils.ts``        — pure helpers + ``VariantBOverrides``
 *                              + sample-context seeder + structural diff
 * - ``dry-run-result.tsx``   — result rendering (status header,
 *                              metrics, schema check, collapsibles,
 *                              error banner)
 * - ``variant-b-editor.tsx`` — Variant A summary + Variant B editor
 * - ``compare-controls.tsx`` — toggle, run-both controls, two-column
 *                              layout, single-mode wrapper
 *
 * Pre-split this file was 882 LOC; post-split it stays under 300.
 */

import { useEffect, useMemo, useState } from "react";

import { Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useDryRunAgent } from "@/hooks/api";
import { isManagedAgent } from "@/lib/managed-agent";
import type { AgentDryRunDraft } from "@/lib/api/types";

import { ManagedDryRunWarning } from "./managed-dry-run-warning";

import {
  CompareColumns,
  CompareControls,
  CompareToggle,
  SingleColumnResults,
} from "./agent-test-panel/compare-controls";
import {
  DEFAULT_CONTEXT,
  diffVariantB,
  sampleContextFromInputs,
  type VariantBOverrides,
} from "./agent-test-panel/diff-utils";

// Re-export ``VariantBOverrides`` so existing callers (``AgentForm``
// etc.) keep importing it from the public module path.
export type { VariantBOverrides };

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
  /**
   * Initial context-text override (#724 part 4). When the user clicks
   * "Open in agent tester" from a run-node detail panel, we seed the
   * context with the input state the node received so the dry-run
   * reflects the same conditions. Editable like any other context.
   * Omitted ⇒ panel falls back to the default sample context.
   */
  initialContextText?: string;
}

export function AgentTestPanel({
  draft,
  draftBlockedReason,
  onPromoteVariantB,
  initialContextText,
}: AgentTestPanelProps) {
  const [contextText, setContextText] = useState(
    initialContextText ?? DEFAULT_CONTEXT,
  );
  // Acknowledgement for managed agents' side-effecting dry-run (#739 slice 3).
  const [sideEffectsAck, setSideEffectsAck] = useState(false);
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

  // Managed (Cortex) agents run a real callable with side effects on dry-run
  // (#739 slice 3) — gate the run buttons behind an explicit acknowledgement.
  const managed = draft !== null && isManagedAgent(draft);
  const runGate = !managed || sideEffectsAck;

  const canRunSingle = draft !== null && parseResult.ok && !dryRunA.isPending && runGate;
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

      {managed ? (
        <ManagedDryRunWarning
          checked={sideEffectsAck}
          onCheckedChange={setSideEffectsAck}
        />
      ) : null}

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
