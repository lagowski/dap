import type { PipelineState } from "@/lib/api/types";

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
