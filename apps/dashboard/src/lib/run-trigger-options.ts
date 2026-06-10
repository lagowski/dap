import type { PipelineState } from "@/lib/api/types";

export type ParsedInitialState =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string };

/**
 * Parse the trigger dialogs' initial-state textarea (#778 Phase 3).
 *
 * Blank input means "empty state"; anything else must parse to a JSON
 * *object* — arrays/scalars are rejected with the same message both
 * dialogs previously hand-rolled.
 */
export function parseInitialState(stateText: string): ParsedInitialState {
  if (stateText.trim().length === 0) {
    return { ok: true, value: {} };
  }
  try {
    const parsed: unknown = JSON.parse(stateText);
    if (typeof parsed !== "object" || parsed == null || Array.isArray(parsed)) {
      throw new Error("initial_state must be a JSON object");
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "Invalid JSON",
    };
  }
}

/**
 * Map the version `<select>` value to an explicit pipeline version.
 *
 * The sentinel option ("current" on the pipeline dialog, "latest" on the
 * runs-page dialog) and any non-numeric value mean "let the engine pick".
 */
export function parsePipelineVersion(
  versionStr: string,
  sentinel: string,
): number | undefined {
  if (versionStr === sentinel) return undefined;
  const parsed = Number(versionStr);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function withAutoApprove(
  initialState: Record<string, unknown>,
  enabled: boolean,
): Partial<PipelineState> {
  if (!enabled) {
    return initialState as Partial<PipelineState>;
  }

  const extensions =
    typeof initialState.extensions === "object" &&
    initialState.extensions !== null &&
    !Array.isArray(initialState.extensions)
      ? initialState.extensions
      : {};

  return {
    ...initialState,
    extensions: {
      ...extensions,
      auto_approve: true,
    },
  } as Partial<PipelineState>;
}
