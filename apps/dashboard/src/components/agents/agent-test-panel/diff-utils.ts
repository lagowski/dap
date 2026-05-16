/**
 * Pure helpers + shared types for the AgentTestPanel split (audit D1).
 *
 * No React, no JSX, no DOM — everything here is safe to import from
 * both the orchestrator and any of the presentational sub-components.
 * Splitting these out kept the parent component file under 400 LOC
 * after the audit-D1 refactor.
 */

import type { AgentDryRunDraft } from "@/lib/api/types";

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

export const DEFAULT_CONTEXT = "{}";

/**
 * Cap on the Variant-A prompt preview rendered inside the compare
 * column header. Long prompts get an ellipsis; the user can read the
 * full version in the Form tab.
 */
export const VARIANT_A_PROMPT_LIMIT = 600;

/**
 * Slug a raw input_schema sample seed: produces a JSON object whose
 * keys match the agent's declared inputs with placeholder values, so
 * the user has a starting point instead of an empty ``{}``.
 */
export function sampleContextFromInputs(inputs: string[]): string {
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
        acc[key] = sortKeysRecursively((value as Record<string, unknown>)[key]);
        return acc;
      }, {});
  }
  return value;
}

export function stableStringify(value: unknown): string {
  return JSON.stringify(sortKeysRecursively(value));
}

export function diffVariantB(
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
