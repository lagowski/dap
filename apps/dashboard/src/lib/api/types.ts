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
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
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
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  constraints?: string[];
  budget_limit_usd?: number | null;
  timeout_ms?: number;
}

export interface AgentUpdate {
  /**
   * PUT /agents/{id} body — full-replacement payload that creates a new
   * immutable version. The engine applies Pydantic defaults for omitted
   * fields (runtime_config={}, input_schema={}, etc.), so partial payloads
   * silently reset stored values. We require everything except `name`
   * (which the engine defaults to the previous version's name) so callers
   * must consciously forward each field.
   */
  name?: string;
  runtime_id: string;
  runtime_config: Record<string, unknown>;
  prompt_template: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
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
