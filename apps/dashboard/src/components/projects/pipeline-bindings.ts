/**
 * Pure helpers for managing pipeline binding maps (kind → pipeline_id).
 * Follows the same pattern as EnvVarsEditor's inline helpers but
 * extracted for testability.
 */

/** Add a new binding with a generated placeholder kind key. */
export function addBinding(
  bindings: Record<string, string>,
): Record<string, string> {
  let candidate = "kind";
  let n = 1;
  while (candidate in bindings) {
    candidate = `kind_${n++}`;
  }
  return { ...bindings, [candidate]: "" };
}

/**
 * Update a binding: rename the kind key and/or change the pipeline_id.
 * Preserves insertion order by rebuilding the record.
 */
export function updateBinding(
  bindings: Record<string, string>,
  oldKind: string,
  newKind: string,
  pipelineId: string,
): Record<string, string> {
  const next: Record<string, string> = {};
  for (const [k, v] of Object.entries(bindings)) {
    if (k === oldKind) {
      if (newKind.length > 0) next[newKind] = pipelineId;
    } else {
      next[k] = v;
    }
  }
  return next;
}

/** Remove a binding by kind key. */
export function removeBinding(
  bindings: Record<string, string>,
  kind: string,
): Record<string, string> {
  const { [kind]: _removed, ...rest } = bindings;
  return rest;
}
