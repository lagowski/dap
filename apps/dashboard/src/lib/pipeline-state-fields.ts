/**
 * Static mirror of `PipelineState` from `packages/types/src/dap_types/state.py`.
 *
 * Used by AgentForm's input/output field pickers (#61) so the user can pick
 * fields by name without having to memorise the schema. The order here
 * matches the source — group headings track the section comments in the
 * Pydantic model.
 *
 * Kept in sync with the engine manually until #50 lands a catalogue
 * endpoint that exposes the schema at runtime. If the backend gains or
 * renames a field, update this file too — the engine's
 * `validate_field_list` validator is the source of truth and will reject
 * an outdated picker selection with a 422.
 */

export type PipelineStateGroup =
  | "Metadata / control"
  | "Task selection"
  | "Test generation"
  | "Execution loop"
  | "Implementation"
  | "Verification"
  | "Final output";

export interface PipelineStateField {
  /** Field name — matches PipelineState attribute exactly. */
  name: string;
  /** Type annotation string — for display only, not runtime parsing. */
  type: string;
  /** UI category — drives section headings in the picker. */
  group: PipelineStateGroup;
  /** One-liner shown beneath the field name. */
  description: string;
  /**
   * Whether the field is required (no default) at the model level.
   * Display-only hint; the engine resolves "born satisfied" semantics
   * itself during pipeline cohesion validation (#59).
   */
  required?: boolean;
}

export const PIPELINE_STATE_FIELDS: readonly PipelineStateField[] = [
  // ---- METADATA / CONTROL ----
  {
    name: "run_id",
    type: "str",
    group: "Metadata / control",
    description: "Unique id for this run. Required — supplied via initial_state.",
    required: true,
  },
  {
    name: "repo",
    type: "str",
    group: "Metadata / control",
    description: "Repository identifier. Required.",
    required: true,
  },
  {
    name: "branch",
    type: "str",
    group: "Metadata / control",
    description: "Working branch. Required.",
    required: true,
  },
  {
    name: "commit_sha",
    type: "str | None",
    group: "Metadata / control",
    description: "Optional commit SHA snapshot of the run start.",
  },

  // ---- TASK SELECTION ----
  {
    name: "available_issues",
    type: "list[dict]",
    group: "Task selection",
    description: "Candidate issues seeded into the run.",
  },
  {
    name: "selected_issue_ids",
    type: "list[int]",
    group: "Task selection",
    description: "Subset of available_issues the run will work on.",
  },

  // ---- TEST GENERATION ----
  {
    name: "tests_generated",
    type: "bool",
    group: "Test generation",
    description: "Whether the test_author has produced tests yet.",
  },
  {
    name: "test_files",
    type: "list[str]",
    group: "Test generation",
    description: "Generated test file paths.",
  },
  {
    name: "test_generation_errors",
    type: "list[str]",
    group: "Test generation",
    description: "Errors raised by the test_author (non-fatal).",
  },

  // ---- EXECUTION LOOP ----
  {
    name: "max_attempts",
    type: "int",
    group: "Execution loop",
    description: "Retry budget for the implement→verify loop. Default 3.",
  },
  {
    name: "attempt",
    type: "int",
    group: "Execution loop",
    description: "Current retry counter (1-based at first attempt).",
  },
  {
    name: "tests_passed",
    type: "bool",
    group: "Execution loop",
    description: "Whether the latest test run passed.",
  },
  {
    name: "last_test_output",
    type: "str",
    group: "Execution loop",
    description: "stdout/stderr from the last test invocation.",
  },

  // ---- IMPLEMENTATION ----
  {
    name: "modified_files",
    type: "list[str]",
    group: "Implementation",
    description: "File paths the implementer changed.",
  },
  {
    name: "implementation_notes",
    type: "str | None",
    group: "Implementation",
    description: "Free-form notes the implementer leaves for the verifier.",
  },

  // ---- VERIFICATION ----
  {
    name: "verification_status",
    type: 'Literal["pending", "approved", "rejected"]',
    group: "Verification",
    description: "Verifier's decision. Default 'pending'.",
  },
  {
    name: "verification_reason",
    type: "str | None",
    group: "Verification",
    description: "Why the verifier approved or rejected.",
  },

  // ---- FINAL OUTPUT ----
  {
    name: "final_status",
    type: 'Literal["running", "success", "failed", "aborted", "paused"]',
    group: "Final output",
    description: "Run-level outcome. Default 'running'.",
  },
] as const;

export const PIPELINE_STATE_GROUPS: readonly PipelineStateGroup[] = [
  "Metadata / control",
  "Task selection",
  "Test generation",
  "Execution loop",
  "Implementation",
  "Verification",
  "Final output",
] as const;

const FIELD_NAMES = new Set(PIPELINE_STATE_FIELDS.map((f) => f.name));

/** Whether `name` corresponds to a known PipelineState field. */
export function isPipelineStateField(name: string): boolean {
  return FIELD_NAMES.has(name);
}

/**
 * Per-role default `output_schema` — mirrors `ROLE_FIELDS` from
 * `packages/types/src/dap_types/role_outputs.py`. Used by AgentForm to
 * pre-populate the output_schema picker when the user creates a new
 * agent for a known role. The user can edit before saving.
 */
export const ROLE_DEFAULT_OUTPUT_SCHEMA: Record<string, readonly string[]> = {
  task_selector: ["selected_issue_ids"],
  test_author: ["tests_generated", "test_files", "test_generation_errors"],
  implementer: ["modified_files", "implementation_notes"],
  verifier: [
    "verification_status",
    "verification_reason",
    "tests_passed",
    "last_test_output",
  ],
};
