import type { PipelineState } from "@/lib/api/types";

export interface StateDiffEntry {
  field: string;
  before: unknown;
  after: unknown;
}

/**
 * Computes a field-by-field diff between two PipelineState objects.
 * Returns only the fields that changed (deep equality via JSON comparison).
 */
export function computeStateDiff(
  before: PipelineState,
  after: PipelineState,
): StateDiffEntry[] {
  const diff: StateDiffEntry[] = [];

  const allKeys = new Set([
    ...Object.keys(before),
    ...Object.keys(after),
  ]) as Set<keyof PipelineState>;

  for (const key of allKeys) {
    const bVal = before[key];
    const aVal = after[key];

    if (!deepEqual(bVal, aVal)) {
      diff.push({ field: key, before: bVal, after: aVal });
    }
  }

  return diff;
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return a === b;
  if (typeof a !== typeof b) return false;
  if (typeof a !== "object") return false;
  return JSON.stringify(a) === JSON.stringify(b);
}
