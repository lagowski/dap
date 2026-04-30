/**
 * Static mirror of `PipelineState` from `packages/types/src/dap_types/state.py`.
 *
 * Used by AgentForm's input/output field pickers (#61) so the user can pick
 * fields by name without having to memorise the schema. The order here
 * matches the source so a reader scanning the picker maps it to the model.
 *
 * Group labels are **UI-facing** and may diverge from the section comments
 * in the Pydantic model when a Python-flavoured name reads badly in the
 * dashboard — e.g. when a backend section name would collide with one of
 * the picker's section headers ("Inputs (read by the prompt)" /
 * "Outputs (written back…)") and read as a contradiction. Keep the field
 * *names* in lock-step with the model; group strings are dashboard-only
 * copy. See ``Pipeline outcome`` below for the current diverging example.
 *
 * Kept in sync with the engine manually for now — there's no catalogue
 * endpoint exposing the schema at runtime yet. If the backend gains or
 * renames a field, update this file too. The engine's `validate_field_list`
 * validator is the source of truth and will reject an outdated picker
 * selection with a 422 — see ``<PipelineStateFieldPicker>`` for the
 * stale-mirror fallback that keeps existing selections editable.
 */

export type PipelineStateGroup =
  | "Metadata / control"
  | "Task selection"
  | "Test generation"
  | "Execution loop"
  | "Implementation"
  | "Verification"
  | "Pipeline outcome";

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
   * Concrete sample value rendered next to the type so operators can
   * see what real data looks like at a glance — much more useful
   * than the type name alone for ``list[dict]`` etc. Plain string,
   * displayed inline as muted monospace.
   */
  example: string;
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
    description: "Unique id for this run. Engine-assigned during run creation.",
    example: '"run_4f3a91c0"',
    required: true,
  },
  {
    name: "repo",
    type: "str",
    group: "Metadata / control",
    description: "Repository identifier. Required.",
    example: '"github.com/acme/api"',
    required: true,
  },
  {
    name: "branch",
    type: "str",
    group: "Metadata / control",
    description: "Working branch. Required.",
    example: '"feature/payment-flow"',
    required: true,
  },
  {
    name: "commit_sha",
    type: "str | None",
    group: "Metadata / control",
    description: "Optional commit SHA snapshot of the run start.",
    example: '"a1b2c3d…" or null',
  },

  // ---- TASK SELECTION ----
  {
    name: "available_issues",
    type: "list[dict]",
    group: "Task selection",
    description: "Candidate issues seeded into the run.",
    example: '[{ id: 42, title: "Bug: rate limit" }]',
  },
  {
    name: "selected_issue_ids",
    type: "list[int]",
    group: "Task selection",
    description: "Subset of available_issues the run will work on.",
    example: "[42, 43]",
  },

  // ---- TEST GENERATION ----
  {
    name: "tests_generated",
    type: "bool",
    group: "Test generation",
    description: "Whether the test_author has produced tests yet.",
    example: "true / false",
  },
  {
    name: "test_files",
    type: "list[str]",
    group: "Test generation",
    description: "Generated test file paths.",
    example: '["tests/test_rate_limit.py"]',
  },
  {
    name: "test_generation_errors",
    type: "list[str]",
    group: "Test generation",
    description: "Errors raised by the test_author (non-fatal).",
    example: '["pytest collection timeout"]',
  },

  // ---- EXECUTION LOOP ----
  {
    name: "max_attempts",
    type: "int",
    group: "Execution loop",
    description: "Retry budget for the implement→verify loop. Default 3.",
    example: "3",
  },
  {
    name: "attempt",
    type: "int",
    group: "Execution loop",
    description: "Current retry counter (0-based; defaults to 0 before the first retry attempt).",
    example: "0 (first try) … 2",
  },
  {
    name: "tests_passed",
    type: "bool",
    group: "Execution loop",
    description: "Whether the latest test run passed.",
    example: "true / false",
  },
  {
    name: "last_test_output",
    type: "str",
    group: "Execution loop",
    description: "stdout/stderr from the last test invocation.",
    example: '"5 passed, 0 failed in 1.2s"',
  },

  // ---- IMPLEMENTATION ----
  {
    name: "modified_files",
    type: "list[str]",
    group: "Implementation",
    description: "File paths the implementer changed.",
    example: '["src/api.py", "tests/test_api.py"]',
  },
  {
    name: "implementation_notes",
    type: "str | None",
    group: "Implementation",
    description: "Free-form notes the implementer leaves for the verifier.",
    example: '"Used asyncio.Lock for concurrency"',
  },

  // ---- VERIFICATION ----
  {
    name: "verification_status",
    type: 'Literal["pending", "approved", "rejected"]',
    group: "Verification",
    description: "Verifier's decision. Default 'pending'.",
    example: '"approved"',
  },
  {
    name: "verification_reason",
    type: "str | None",
    group: "Verification",
    description: "Why the verifier approved or rejected.",
    example: '"Tests pass, scope contained."',
  },

  // ---- PIPELINE OUTCOME ----
  // UI-facing group name (#122) — picked so it doesn't collide with
  // the "Outputs (written back…)" section header in the picker.
  {
    name: "final_status",
    type: 'Literal["running", "success", "failed", "aborted", "paused"]',
    group: "Pipeline outcome",
    description: "Run-level outcome. Default 'running'.",
    example: '"success"',
  },
] as const;

export const PIPELINE_STATE_GROUPS: readonly PipelineStateGroup[] = [
  "Metadata / control",
  "Task selection",
  "Test generation",
  "Execution loop",
  "Implementation",
  "Verification",
  "Pipeline outcome",
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

/**
 * Per-role conventional `input_schema` — UX hint surfaced as the
 * "Use role defaults" pre-fill on the Inputs picker. Derived from
 * the typical pipeline graph: each role consumes the upstream
 * outputs it needs to do its job.
 *
 * - ``task_selector``: candidate issues + retry budget.
 * - ``test_author``: which issue(s) the suite should target.
 * - ``implementer``: the same selection + any implementation notes
 *   the verifier left from a previous attempt.
 * - ``verifier``: the test run result + what the implementer touched.
 *
 * Roles without an entry (``post_check``, ``prompt_builder``, custom)
 * intentionally have no recommendation — the form hides the hint
 * block and leaves the picker empty for the user to populate.
 *
 * The user is free to deviate; this just fills the picker with a
 * sensible starting point.
 */
export const ROLE_DEFAULT_INPUT_SCHEMA: Record<string, readonly string[]> = {
  task_selector: ["available_issues", "max_attempts"],
  test_author: ["selected_issue_ids"],
  implementer: ["selected_issue_ids", "implementation_notes"],
  verifier: [
    "tests_passed",
    "last_test_output",
    "modified_files",
    "implementation_notes",
  ],
};
