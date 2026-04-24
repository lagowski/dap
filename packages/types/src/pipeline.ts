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

export interface PipelineNode {
  id: string;
  agent_id: string;
  position: { x: number; y: number };
  overrides?: {
    runtime_config?: Record<string, unknown>;
    budget_limit_usd?: number;
    timeout_ms?: number;
  };
}

export interface PipelineEdge {
  id: string;
  source: string;
  target: string;
  condition?: EdgeCondition;
  label?: string;
}

export type EdgeCondition =
  | ComparisonCondition
  | LogicalCondition;

export interface ComparisonCondition {
  type: "comparison";
  field: string;
  operator: "==" | "!=" | "<" | "<=" | ">" | ">=";
  value: string | number | boolean | null;
}

export interface LogicalCondition {
  type: "and" | "or";
  children: EdgeCondition[];
}
