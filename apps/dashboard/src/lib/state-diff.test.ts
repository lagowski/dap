import { describe, expect, it } from "vitest";
import { computeStateDiff } from "./state-diff";
import type { PipelineState } from "@/lib/api/types";

function makeState(overrides: Partial<PipelineState> = {}): PipelineState {
  return {
    run_id: "run-1",
    repo: "owner/repo",
    branch: "main",
    commit_sha: null,
    available_issues: [],
    selected_issue_ids: [],
    tests_generated: false,
    test_files: [],
    test_generation_errors: [],
    max_attempts: 3,
    attempt: 0,
    tests_passed: false,
    last_test_output: "",
    modified_files: [],
    implementation_notes: null,
    verification_status: "pending",
    verification_reason: null,
    final_status: "running",
    ...overrides,
  };
}

describe("computeStateDiff", () => {
  it("returns empty diff for identical states", () => {
    const state = makeState();
    expect(computeStateDiff(state, state)).toHaveLength(0);
  });

  it("detects changed scalar field", () => {
    const before = makeState({ attempt: 1 });
    const after = makeState({ attempt: 2 });
    const diff = computeStateDiff(before, after);
    expect(diff).toContainEqual({ field: "attempt", before: 1, after: 2 });
  });

  it("detects changed array field", () => {
    const before = makeState({ modified_files: [] });
    const after = makeState({ modified_files: ["src/foo.ts"] });
    const diff = computeStateDiff(before, after);
    expect(diff).toContainEqual({
      field: "modified_files",
      before: [],
      after: ["src/foo.ts"],
    });
  });

  it("detects changed object array field", () => {
    const before = makeState({ available_issues: [] });
    const after = makeState({ available_issues: [{ id: 1 }] });
    const diff = computeStateDiff(before, after);
    expect(diff).toContainEqual({
      field: "available_issues",
      before: [],
      after: [{ id: 1 }],
    });
  });

  it("handles null-to-value transition", () => {
    const before = makeState({ commit_sha: null });
    const after = makeState({ commit_sha: "abc123" });
    const diff = computeStateDiff(before, after);
    expect(diff).toContainEqual({
      field: "commit_sha",
      before: null,
      after: "abc123",
    });
  });

  it("handles multiple simultaneous changes", () => {
    const before = makeState({
      tests_passed: false,
      last_test_output: "",
      test_files: [],
    });
    const after = makeState({
      tests_passed: true,
      last_test_output: "All passed",
      test_files: ["test_main.py"],
    });
    const diff = computeStateDiff(before, after);
    expect(diff).toHaveLength(3);
  });

  it("ignores unchanged fields among changed ones", () => {
    const before = makeState({ final_status: "running" });
    const after = makeState({ final_status: "success" });
    const diff = computeStateDiff(before, after);
    expect(diff).toHaveLength(1);
    expect(diff[0]).toEqual({
      field: "final_status",
      before: "running",
      after: "success",
    });
  });
});
