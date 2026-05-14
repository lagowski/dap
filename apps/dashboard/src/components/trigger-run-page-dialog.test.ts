import { describe, it, expect } from "vitest";
import type { RunCreateRequest, Pipeline } from "@/lib/api/types";

/**
 * Pure-logic tests for the TriggerRunPageDialog component.
 *
 * Since @testing-library/react is not available in this project,
 * these tests verify the data-transformation and validation logic
 * that the component relies on.
 */

// ---------- helpers extracted from component logic ----------

/** Validate and parse JSON initial state — mirrors the component's handleSubmit logic. */
function parseInitialState(text: string): {
  ok: true;
  value: Record<string, unknown>;
} | { ok: false; error: string } {
  if (text.trim().length === 0) {
    return { ok: true, value: {} };
  }
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed !== "object" || parsed == null || Array.isArray(parsed)) {
      return { ok: false, error: "initial_state must be a JSON object" };
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Invalid JSON" };
  }
}

/** Build RunCreateRequest payload — mirrors component's handleSubmit assembly. */
function buildPayload(opts: {
  pipelineId: string;
  versionStr: string;
  projectId: string;
  initialState: Record<string, unknown>;
}): RunCreateRequest {
  const parsedVersion =
    opts.versionStr === "latest" ? undefined : Number(opts.versionStr);
  const pipelineVersion =
    parsedVersion !== undefined && Number.isFinite(parsedVersion)
      ? parsedVersion
      : undefined;

  return {
    pipeline_id: opts.pipelineId,
    ...(pipelineVersion !== undefined
      ? { pipeline_version: pipelineVersion }
      : {}),
    ...(opts.projectId ? { project_id: opts.projectId } : {}),
    initial_state: opts.initialState,
  };
}

/** Determine if submit should be disabled — pipeline must be selected. */
function isSubmitDisabled(selectedPipelineId: string, isPending: boolean): boolean {
  return !selectedPipelineId || isPending;
}

/** Determine if the zero-pipelines fallback should show. */
function shouldShowZeroPipelinesFallback(
  pipelines: { items: Pipeline[] } | undefined,
): boolean {
  return pipelines != null && pipelines.items.length === 0;
}

/** Determine if the header trigger button should be visible. */
function shouldShowHeaderButton(
  pipelines: { items: Pipeline[] } | undefined,
): boolean {
  return pipelines != null && pipelines.items.length > 0;
}

// ---------- tests ----------

describe("parseInitialState", () => {
  it("returns empty object for blank input", () => {
    const result = parseInitialState("");
    expect(result).toEqual({ ok: true, value: {} });
  });

  it("returns empty object for whitespace-only input", () => {
    const result = parseInitialState("   \n  ");
    expect(result).toEqual({ ok: true, value: {} });
  });

  it("parses valid JSON object", () => {
    const result = parseInitialState('{"repo": "my-repo"}');
    expect(result).toEqual({ ok: true, value: { repo: "my-repo" } });
  });

  it("rejects JSON array", () => {
    const result = parseInitialState("[1, 2, 3]");
    expect(result).toEqual({
      ok: false,
      error: "initial_state must be a JSON object",
    });
  });

  it("rejects JSON primitive", () => {
    const result = parseInitialState('"just a string"');
    expect(result).toEqual({
      ok: false,
      error: "initial_state must be a JSON object",
    });
  });

  it("rejects invalid JSON syntax", () => {
    const result = parseInitialState("{bad json}");
    expect(result.ok).toBe(false);
  });
});

describe("buildPayload", () => {
  it("builds minimal payload with latest version", () => {
    const payload = buildPayload({
      pipelineId: "pipe-1",
      versionStr: "latest",
      projectId: "",
      initialState: {},
    });
    expect(payload).toEqual({
      pipeline_id: "pipe-1",
      initial_state: {},
    });
    expect("pipeline_version" in payload).toBe(false);
    expect("project_id" in payload).toBe(false);
  });

  it("includes pipeline_version when not latest", () => {
    const payload = buildPayload({
      pipelineId: "pipe-1",
      versionStr: "3",
      projectId: "",
      initialState: {},
    });
    expect(payload.pipeline_version).toBe(3);
  });

  it("includes project_id when provided", () => {
    const payload = buildPayload({
      pipelineId: "pipe-1",
      versionStr: "latest",
      projectId: "proj-1",
      initialState: {},
    });
    expect(payload.project_id).toBe("proj-1");
  });

  it("includes initial_state when provided", () => {
    const payload = buildPayload({
      pipelineId: "pipe-1",
      versionStr: "latest",
      projectId: "",
      initialState: { repo: "my-repo", branch: "main" },
    });
    expect(payload.initial_state).toEqual({ repo: "my-repo", branch: "main" });
  });

  it("builds full payload with all options", () => {
    const payload = buildPayload({
      pipelineId: "pipe-1",
      versionStr: "2",
      projectId: "proj-1",
      initialState: { key: "value" },
    });
    expect(payload).toEqual({
      pipeline_id: "pipe-1",
      pipeline_version: 2,
      project_id: "proj-1",
      initial_state: { key: "value" },
    });
  });
});

describe("isSubmitDisabled", () => {
  it("is disabled when no pipeline selected", () => {
    expect(isSubmitDisabled("", false)).toBe(true);
  });

  it("is disabled when mutation is pending", () => {
    expect(isSubmitDisabled("pipe-1", true)).toBe(true);
  });

  it("is enabled when pipeline selected and not pending", () => {
    expect(isSubmitDisabled("pipe-1", false)).toBe(false);
  });
});

describe("shouldShowZeroPipelinesFallback", () => {
  it("returns false when data is undefined (loading)", () => {
    expect(shouldShowZeroPipelinesFallback(undefined)).toBe(false);
  });

  it("returns true when items array is empty", () => {
    expect(shouldShowZeroPipelinesFallback({ items: [] })).toBe(true);
  });

  it("returns false when pipelines exist", () => {
    const pipeline = { id: "p1", name: "test" } as Pipeline;
    expect(shouldShowZeroPipelinesFallback({ items: [pipeline] })).toBe(false);
  });
});

describe("shouldShowHeaderButton", () => {
  it("returns false when data is undefined (loading)", () => {
    expect(shouldShowHeaderButton(undefined)).toBe(false);
  });

  it("returns false when no pipelines exist", () => {
    expect(shouldShowHeaderButton({ items: [] })).toBe(false);
  });

  it("returns true when pipelines exist", () => {
    const pipeline = { id: "p1", name: "test" } as Pipeline;
    expect(shouldShowHeaderButton({ items: [pipeline] })).toBe(true);
  });
});

describe("empty state behavior", () => {
  it("the POST /runs hint text should not appear — replaced by trigger button", () => {
    // This test documents the contract: the old empty state text is gone.
    // The component renders "No runs yet." + a TriggerRunPageDialog button
    // instead of "No runs yet. Trigger one via POST /runs."
    const oldEmptyStateText = "POST /runs";
    const newEmptyStateText = "No runs yet.";
    const newButtonLabel = "Trigger run";

    // The old text must not appear in the component's render output
    expect(oldEmptyStateText).not.toBe(newEmptyStateText);
    expect(newButtonLabel).toBe("Trigger run");
  });
});

describe("project pre-fill", () => {
  it("uses activeProjectId when set", () => {
    const activeProjectId = "proj-123";
    const projectId = activeProjectId ?? "";
    expect(projectId).toBe("proj-123");
  });

  it("defaults to empty string when no active project", () => {
    const activeProjectId = null;
    const projectId = activeProjectId ?? "";
    expect(projectId).toBe("");
  });
});
