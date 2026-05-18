/**
 * Dashboard API type facade.
 *
 * Server-owned request/response contracts are generated into
 * `types.gen.ts` from the engine OpenAPI schema (`pnpm gen:api`).
 * This file keeps dashboard-only convenience aliases and tightens a
 * few defaulted response fields that the UI receives as normalized values.
 */

import type { components } from "./types.gen";

type ApiSchema<Name extends keyof components["schemas"]> = components["schemas"][Name];

export type AgentRole =
  | "task_selector"
  | "prompt_builder"
  | "test_author"
  | "implementer"
  | "verifier"
  | "post_check"
  | string;

type AgentPayloadDefaults = {
  runtime_config: Record<string, unknown>;
  input_schema: string[];
  output_schema: string[];
  constraints: string[];
  budget_limit_usd: number | null;
  timeout_ms: number;
};

export type Agent = Omit<
  ApiSchema<"Agent">,
  keyof AgentPayloadDefaults | "role"
> &
  AgentPayloadDefaults & {
    role: AgentRole;
    // Populated only on list responses. null on detail/create/update endpoints;
    // undefined on responses from older engines that predate the field.
    used_in_pipelines?: number | null;
  };

export type AgentCreate = Omit<
  ApiSchema<"AgentCreate">,
  "timeout_ms" | "role"
> & {
  role: string;
  timeout_ms?: number;
};

// ---- Agent dry-run (#103 / #104) ----

/**
 * Inline agent definition used by ``POST /agents/dry-run`` when the
 * caller hasn't persisted the agent yet (the New / dirty Edit form).
 * Mirrors the server's ``AgentDryRunDraft`` field-by-field.
 */
export type AgentDryRunDraft = Omit<
  ApiSchema<"AgentDryRunDraft">,
  keyof AgentPayloadDefaults | "role"
> &
  AgentPayloadDefaults & {
    role: string;
  };

/**
 * Body of ``POST /agents/dry-run`` — exactly one of ``agent_id`` (saved)
 * or ``draft`` (inline) must be set. ``context`` is the sample state
 * the prompt template renders against.
 */
export type AgentDryRunRequest = Omit<
  ApiSchema<"AgentDryRunRequest">,
  "context" | "draft"
> & {
  draft?: AgentDryRunDraft | null;
  context: Record<string, unknown>;
};

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
export type AgentExportPayload = Omit<
  ApiSchema<"AgentExportPayload">,
  keyof AgentPayloadDefaults | "role"
> &
  AgentPayloadDefaults & {
    role: string;
  };

export type AgentExport = Omit<ApiSchema<"AgentExport">, "agent"> & {
  agent: AgentExportPayload;
};

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
    requires_terminal_final_status?: boolean;
  };
  /** Dashboard layout metadata (node positions etc.) — preserved through export/import (#226). */
  ui_metadata?: Record<string, unknown>;
}

export interface PipelineExport {
  schema_version: typeof PIPELINE_EXPORT_SCHEMA_VERSION;
  pipeline: PipelineExportPayload;
  /**
   * Optional bundle (#126) — when present, the importer creates the
   * agents first, builds an old→new id remap, and rewrites every
   * ``node.agent_id`` in the pipeline payload before persisting.
   * Keys are the *source* installation's agent ids.
   *
   * Field is fully **optional**: pipeline-only exports (Phase 1
   * shape, ``?bundle=false``) omit it entirely from the JSON
   * response (the engine sets ``response_model_exclude_none=True``).
   * Bundle exports include it as a non-null record. Treat both
   * ``undefined`` and a missing key as "no bundle".
   */
  bundled_agents?: Record<string, AgentExportPayload>;
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

// ---- Env var validation (#350) ----

export interface ValidateEnvRequest {
  env_vars: Record<string, string>;
}

export interface EnvVarValidationResult {
  key: string;
  is_token: boolean;
  valid: boolean | null;
  login: string | null;
  error: string | null;
}

export interface ValidateEnvResponse {
  results: EnvVarValidationResult[];
}

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
    requires_terminal_final_status?: boolean;
  };
  created_at: string;
  updated_at: string;
  is_active: boolean;
  /** Dashboard-private layout metadata (node positions, etc.). Not used by the engine. */
  ui_metadata?: Record<string, unknown>;
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
  extensions: Record<string, unknown>;  // per-pipeline extra state (#64)
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
  /** Gate node the run is staged before when final_status=="paused" (#363). */
  paused_at_node: string | null;
  /** Task assignments + optional spec stored at gate interrupt time (#364). */
  gate_payload: {
    task_assignments?: Array<{
      /** Cortex dispatcher emits this as "task" (see dispatcher.py). */
      task: string;
      agent: string;
      priority?: string;
    }>;
    spec?: string;
  } | null;
  node_statuses: Record<string, NodeStatus>;
  final_status: FinalStatus;
  /**
   * Operator-facing reason for a non-clean termination (#260).
   * Populated when the engine forcibly fails an orphan run on
   * restart; `null` for runs that finished normally.
   */
  failure_reason: string | null;
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
    requires_terminal_final_status?: boolean;
  };
  ui_metadata?: Record<string, unknown>;
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
    requires_terminal_final_status?: boolean;
  };
  ui_metadata?: Record<string, unknown>;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

// ---------------------------------------------------------------------------
// Auth (Phase B, #300)
// ---------------------------------------------------------------------------

/**
 * Shape returned by ``GET /users/me`` — fastapi-users' default user
 * model plus our additions. Matches ``UserORM`` in the engine.
 */
export interface CurrentUser {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterCredentials {
  email: string;
  password: string;
}

// ---------------------------------------------------------------------------
// Admin — Users (#301, sub-C2)
// ---------------------------------------------------------------------------

/**
 * Admin-only user representation returned by ``GET /users`` (the
 * admin list endpoint). Wider than ``CurrentUser`` — exposes
 * ``created_at`` / ``last_login_at`` / ``deleted_at`` so the admin
 * table can show "joined", "last active", and the soft-delete state.
 */
export interface AdminUser {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  created_at: string | null;
  last_login_at: string | null;
  deleted_at: string | null;
}

/**
 * Fields the dashboard's "edit user" actions toggle. Matches
 * fastapi-users' ``BaseUserUpdate`` schema (PATCH ``/users/{id}``).
 */
export interface AdminUserUpdate {
  is_active?: boolean;
  is_superuser?: boolean;
  is_verified?: boolean;
}

// ---------------------------------------------------------------------------
// Admin — Audit log (#301, sub-C3)
// ---------------------------------------------------------------------------

/**
 * One row in the audit log. ``user_id`` and ``event_data`` are
 * nullable on the column — keep that here so the table can render
 * legitimate "no actor" events (pre-auth failures, system events)
 * without faking values.
 */
export interface AuditEvent {
  id: string;
  user_id: string | null;
  event_type: string;
  event_data: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditEventFilters {
  eventType?: string;
  userId?: string;
}

// ---------------------------------------------------------------------------
// Admin — API tokens (#301, sub-C4)
// ---------------------------------------------------------------------------

/**
 * Admin view of an API token. Wider than the user-scoped shape
 * (``ApiTokenRead`` on the engine) — adds ``owner_id`` + ``owner_email``
 * so the admin table can render the owner column without a separate
 * lookup per row.
 *
 * ``prefix`` is the 8-char indexed prefix of the raw token (the
 * substring *after* ``dap_``); the raw token itself only exists in
 * the response of the create endpoint.
 */
export interface AdminApiToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  owner_id: string;
  owner_email: string;
}

// ---------------------------------------------------------------------------
// Admin — Instance settings (#301, sub-C5)
// ---------------------------------------------------------------------------

/**
 * Read-only snapshot of instance-wide engine configuration the admin
 * panel surfaces. Secrets are presence-only — the engine never echoes
 * the actual JWT secret or OAuth client_secret values.
 */
export interface AdminOAuthProvider {
  configured: boolean;
  client_id_configured: boolean;
  client_secret_configured: boolean;
}

export interface AdminInstanceSettings {
  auth: {
    jwt_secret_configured: boolean;
    access_ttl_seconds: number;
    log_reset_tokens: boolean;
  };
  oauth: {
    github: AdminOAuthProvider;
    google: AdminOAuthProvider;
    redirect_url: string | null;
  };
  cors: {
    /**
     * The *effective* allow-list — what ``CORSMiddleware`` actually
     * uses. When the operator left ``DAP_CORS_ORIGINS`` unset this
     * is the engine's default local-dev list, **not** an empty/
     * permissive policy. ``using_default`` disambiguates the two.
     */
    origins: string[];
    /**
     * ``true`` when ``DAP_CORS_ORIGINS`` was unset and the engine
     * fell back to ``DEFAULT_CORS_ORIGINS``. The dashboard renders
     * a warning chip in this state — production deployments
     * should set ``DAP_CORS_ORIGINS`` explicitly.
     */
    using_default: boolean;
  };
  storage: {
    backend: "sqlite" | "postgresql";
    location: string;
  };
}
