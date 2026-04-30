/**
 * Hand-rolled mirror of dap_types Pydantic models.
 *
 * For full type sync run `pnpm gen:api` (requires running engine on
 * NEXT_PUBLIC_DAP_ENGINE_URL) — this writes types.gen.ts. The hand-rolled
 * shapes here are a stable subset used by F6 MVP screens.
 */

export type AgentRole =
  | "task_selector"
  | "prompt_builder"
  | "test_author"
  | "implementer"
  | "verifier"
  | "post_check"
  | string;

export interface Agent {
  id: string;
  name: string;
  role: AgentRole;
  version: number;
  runtime_id: string;
  runtime_config: Record<string, unknown>;
  prompt_template: string;
  input_schema: string[];
  output_schema: string[];
  constraints: string[];
  budget_limit_usd: number | null;
  timeout_ms: number;
  created_at: string;
  updated_at: string;
  is_active: boolean;
}

export interface AgentCreate {
  name: string;
  role: string;
  runtime_id: string;
  runtime_config?: Record<string, unknown>;
  prompt_template: string;
  input_schema?: string[];
  output_schema?: string[];
  constraints?: string[];
  budget_limit_usd?: number | null;
  timeout_ms?: number;
}

// ---- Agent dry-run (#103 / #104) ----

/**
 * Inline agent definition used by ``POST /agents/dry-run`` when the
 * caller hasn't persisted the agent yet (the New / dirty Edit form).
 * Mirrors the server's ``AgentDryRunDraft`` field-by-field.
 */
export interface AgentDryRunDraft {
  name: string;
  role: string;
  runtime_id: string;
  runtime_config: Record<string, unknown>;
  prompt_template: string;
  input_schema: string[];
  output_schema: string[];
  constraints: string[];
  budget_limit_usd: number | null;
  timeout_ms: number;
}

/**
 * Body of ``POST /agents/dry-run`` — exactly one of ``agent_id`` (saved)
 * or ``draft`` (inline) must be set. ``context`` is the sample state
 * the prompt template renders against.
 */
export interface AgentDryRunRequest {
  agent_id?: string;
  agent_version?: number;
  draft?: AgentDryRunDraft;
  context: Record<string, unknown>;
}

/**
 * Soft check of the runtime's structured payload against the agent's
 * declared ``output_schema``. ``checked=false`` means we couldn't run
 * the check (no schema declared, or runtime returned plain text); a
 * missing-fields list with ``valid=false`` is informational, not fatal.
 */
export interface OutputSchemaValidation {
  valid: boolean;
  checked: boolean;
  missing_fields: string[];
  extra_fields: string[];
  note?: string | null;
}

/**
 * Response from ``POST /agents/dry-run``. ``runtime_result`` mirrors
 * the engine's ``RuntimeResult`` shape — kept as ``Record<string, unknown>``
 * here because the structured payload varies per runtime.
 */
export interface AgentDryRunResponse {
  rendered_xml: string;
  prompt_warnings: string[];
  prompt_errors: string[];
  runtime_result: {
    success: boolean;
    output: string;
    structured: Record<string, unknown> | null;
    files_changed: string[];
    tokens_used: number | null;
    cost_usd: number | null;
    duration_ms: number;
    errors: string[];
  };
  output_schema_validation: OutputSchemaValidation;
}

// ---- Agent import / export (#94) ----

/** Stable export schema version. Bump only when the shape changes incompatibly. */
export const AGENT_EXPORT_SCHEMA_VERSION = "agent-export/1";

/**
 * Portable subset of an agent — no per-installation fields. Mirrors
 * ``AgentExportPayload`` on the server.
 */
export interface AgentExportPayload {
  name: string;
  role: string;
  runtime_id: string;
  runtime_config: Record<string, unknown>;
  prompt_template: string;
  input_schema: string[];
  output_schema: string[];
  constraints: string[];
  budget_limit_usd: number | null;
  timeout_ms: number;
}

export interface AgentExport {
  schema_version: string;
  agent: AgentExportPayload;
}

// ---- Pipeline import / export (#124) ----

/** Stable export schema version. Bump only when the shape changes incompatibly. */
export const PIPELINE_EXPORT_SCHEMA_VERSION = "pipeline-export/1";

/**
 * Portable subset of a pipeline — no per-installation fields. Mirrors
 * ``PipelineExportPayload`` on the server. ``node.agent_id`` references
 * point at agent ids in the *source* installation; importing into a
 * different DB requires those agents to exist (or 422 from the
 * validator). Bundling the referenced agents into the envelope so
 * import auto-creates them is a separate follow-up.
 */
export interface PipelineExportPayload {
  name: string;
  description: string;
  schema_version: "langgraph/1.0";
  state_schema_ref: string;
  entry_point: string;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  defaults: {
    max_attempts: number;
    budget_limit_usd: number;
    approval_required_nodes: string[];
  };
}

export interface PipelineExport {
  schema_version: string;
  pipeline: PipelineExportPayload;
}

// ---- Projects (#63 / #67) ----

/**
 * Project = workspace abstraction. Owns a working directory or remote
 * repo, default branch, project-scoped env vars, and a free-form
 * mapping of workflow kinds → pipeline ids.
 */
export interface Project {
  id: string;
  name: string;
  description: string;
  working_directory: string | null;
  repo_url: string | null;
  default_branch: string;
  /** Workflow kind → pipeline_id. Recommended kinds get first-class UI. */
  pipelines: Record<string, string>;
  /** Project-scoped env (#65). Layered onto subprocess env. */
  env_vars: Record<string, string>;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  is_active: boolean;
}

export interface ProjectCreate {
  name: string;
  description?: string;
  working_directory?: string | null;
  repo_url?: string | null;
  default_branch?: string;
  pipelines?: Record<string, string>;
  env_vars?: Record<string, string>;
}

export interface ProjectUpdate {
  /**
   * PUT /projects/{id} body — full-replacement payload. The engine
   * applies Pydantic defaults for omitted fields (description="",
   * default_branch="main", pipelines/env_vars={}), so partial payloads
   * silently reset stored values. Mirroring ``AgentUpdate``'s rationale:
   * we require everything (except the path-bound id) so callers must
   * consciously forward each field.
   */
  name: string;
  description: string;
  working_directory: string | null;
  repo_url: string | null;
  default_branch: string;
  pipelines: Record<string, string>;
  env_vars: Record<string, string>;
}

export interface ProjectRunRequest {
  pipeline_version?: number | null;
  initial_state?: Partial<PipelineState>;
}

/**
 * Recommended workflow kinds — UX hint for which slots to surface
 * first-class on the project detail page. Not enforced by the engine;
 * users can declare custom kinds.
 */
export const RECOMMENDED_PIPELINE_KINDS = [
  "configure",
  "plan",
  "develop",
  "verify",
  "release",
] as const;

export type RecommendedPipelineKind = (typeof RECOMMENDED_PIPELINE_KINDS)[number];

// ---- Settings (dashboard /settings page) ----

export interface RuntimeStatus {
  id: string;
  display_name: string;
  kind: string;
  available: boolean;
  version: string | null;
  missing: string[] | null;
}

export interface ProviderStatus {
  id: string;
  display_name: string;
  default_env_var: string | null;
  configured: boolean;
}

export interface EngineInfo {
  version: string;
  db_path: string;
  checkpoint_db_path: string;
  recursion_limit: number;
}

export interface SettingsView {
  runtimes: RuntimeStatus[];
  providers: ProviderStatus[];
  engine: EngineInfo;
}

export interface AgentUpdate {
  /**
   * PUT /agents/{id} body — full-replacement payload that creates a new
   * immutable version. The engine applies Pydantic defaults for omitted
   * fields (runtime_config={}, input_schema=[], etc.), so partial payloads
   * silently reset stored values. We require everything except `name`
   * (which the engine defaults to the previous version's name) so callers
   * must consciously forward each field.
   */
  name?: string;
  runtime_id: string;
  runtime_config: Record<string, unknown>;
  prompt_template: string;
  input_schema: string[];
  output_schema: string[];
  constraints: string[];
  budget_limit_usd: number | null;
  timeout_ms: number;
}

export type ComparisonOperator = "==" | "!=" | "<" | "<=" | ">" | ">=";

export interface ComparisonCondition {
  type: "comparison";
  field: string;
  operator: ComparisonOperator;
  value: string | number | boolean | null;
}

export interface LogicalCondition {
  type: "and" | "or";
  children: EdgeCondition[];
}

export type EdgeCondition = ComparisonCondition | LogicalCondition;

export interface PipelineNode {
  id: string;
  agent_id: string;
  position: { x: number; y: number };
  overrides?: {
    runtime_config?: Record<string, unknown>;
    budget_limit_usd?: number;
    timeout_ms?: number;
  } | null;
}

export interface PipelineEdge {
  id: string;
  source: string;
  target: string;
  condition?: EdgeCondition | null;
  label?: string | null;
}

export interface Pipeline {
  id: string;
  name: string;
  description: string;
  version: number;
  schema_version: "langgraph/1.0";
  state_schema_ref: string;
  entry_point: string;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  defaults: {
    max_attempts: number;
    budget_limit_usd: number;
    approval_required_nodes: string[];
  };
  created_at: string;
  updated_at: string;
  is_active: boolean;
}

export type FinalStatus = "running" | "success" | "failed" | "aborted" | "paused";

export type NodeStatus = "pending" | "running" | "success" | "failed" | "skipped";

export interface PipelineState {
  run_id: string;
  repo: string;
  branch: string;
  commit_sha: string | null;
  available_issues: Record<string, unknown>[];
  selected_issue_ids: number[];
  tests_generated: boolean;
  test_files: string[];
  test_generation_errors: string[];
  max_attempts: number;
  attempt: number;
  tests_passed: boolean;
  last_test_output: string;
  modified_files: string[];
  implementation_notes: string | null;
  verification_status: "pending" | "approved" | "rejected";
  verification_reason: string | null;
  final_status: FinalStatus;
}

export interface Run {
  id: string;
  /**
   * Owning project (#64). `null` for ad-hoc runs triggered directly
   * via POST /runs without a project association.
   */
  project_id: string | null;
  pipeline_id: string;
  pipeline_version: number;
  trigger_source: "dashboard" | "cli" | "api";
  initial_state: PipelineState;
  current_node: string | null;
  node_statuses: Record<string, NodeStatus>;
  final_status: FinalStatus;
  started_at: string;
  ended_at: string | null;
  tokens_used: number;
  cost_usd: number;
}

export interface NodeExecutionLog {
  id: string;
  run_id: string;
  node_id: string;
  agent_id: string;
  runtime_id: string;
  started_at: string;
  ended_at: string | null;
  prompt_xml: string;
  stdout: string;
  stderr: string;
  output_json: Record<string, unknown> | null;
  tokens_used: number;
  cost_usd: number;
  duration_ms: number;
  status: NodeStatus;
  error_message: string | null;
}

export interface StateSnapshot {
  id: string;
  run_id: string;
  node_id: string;
  timestamp: string;
  state: PipelineState;
}

export interface PaginatedList<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export interface RunCreateRequest {
  pipeline_id: string;
  pipeline_version?: number | null;
  /**
   * Stamp the run with this project (#64). Omit for ad-hoc runs.
   * Engine validates the project exists and is not archived
   * (otherwise 422).
   */
  project_id?: string | null;
  initial_state?: Partial<PipelineState>;
}

export interface PipelineCreate {
  name: string;
  description?: string;
  schema_version: "langgraph/1.0";
  state_schema_ref: string;
  entry_point: string;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  defaults: {
    max_attempts: number;
    budget_limit_usd: number;
    approval_required_nodes: string[];
  };
}

export interface PipelineUpdate {
  name?: string | null;
  description?: string | null;
  schema_version: "langgraph/1.0";
  state_schema_ref: string;
  entry_point: string;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  defaults: {
    max_attempts: number;
    budget_limit_usd: number;
    approval_required_nodes: string[];
  };
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}
