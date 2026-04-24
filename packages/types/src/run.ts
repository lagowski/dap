import type { PipelineState, FinalStatus } from "./state.js";

export type NodeStatus = "pending" | "running" | "success" | "failed" | "skipped";

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
