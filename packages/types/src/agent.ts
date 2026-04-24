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
