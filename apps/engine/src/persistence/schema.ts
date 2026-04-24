import { sqliteTable, text, integer, real } from "drizzle-orm/sqlite-core";

export const agents = sqliteTable("agents", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  role: text("role").notNull(),
  version: integer("version").notNull(),
  runtimeId: text("runtime_id").notNull(),
  runtimeConfig: text("runtime_config", { mode: "json" }).notNull(),
  promptTemplate: text("prompt_template").notNull(),
  inputSchema: text("input_schema", { mode: "json" }).notNull(),
  outputSchema: text("output_schema", { mode: "json" }).notNull(),
  constraints: text("constraints", { mode: "json" }).notNull(),
  budgetLimitUsd: real("budget_limit_usd"),
  timeoutMs: integer("timeout_ms").notNull(),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  isActive: integer("is_active", { mode: "boolean" }).notNull().default(true),
});

export const pipelines = sqliteTable("pipelines", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  description: text("description").notNull().default(""),
  version: integer("version").notNull(),
  schemaVersion: text("schema_version").notNull().default("langgraph/1.0"),
  stateSchemaRef: text("state_schema_ref").notNull(),
  entryPoint: text("entry_point").notNull(),
  nodes: text("nodes", { mode: "json" }).notNull(),
  edges: text("edges", { mode: "json" }).notNull(),
  defaults: text("defaults", { mode: "json" }).notNull(),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  isActive: integer("is_active", { mode: "boolean" }).notNull().default(true),
});

export const runs = sqliteTable("runs", {
  id: text("id").primaryKey(),
  pipelineId: text("pipeline_id").notNull(),
  pipelineVersion: integer("pipeline_version").notNull(),
  triggerSource: text("trigger_source").notNull(),
  initialState: text("initial_state", { mode: "json" }).notNull(),
  currentNode: text("current_node"),
  nodeStatuses: text("node_statuses", { mode: "json" }).notNull(),
  finalStatus: text("final_status").notNull(),
  startedAt: text("started_at").notNull(),
  endedAt: text("ended_at"),
  tokensUsed: integer("tokens_used").notNull().default(0),
  costUsd: real("cost_usd").notNull().default(0),
});

export const stateSnapshots = sqliteTable("state_snapshots", {
  id: text("id").primaryKey(),
  runId: text("run_id").notNull().references(() => runs.id),
  nodeId: text("node_id").notNull(),
  timestamp: text("timestamp").notNull(),
  state: text("state", { mode: "json" }).notNull(),
});

export const nodeExecutionLogs = sqliteTable("node_execution_logs", {
  id: text("id").primaryKey(),
  runId: text("run_id").notNull().references(() => runs.id),
  nodeId: text("node_id").notNull(),
  agentId: text("agent_id").notNull(),
  runtimeId: text("runtime_id").notNull(),
  startedAt: text("started_at").notNull(),
  endedAt: text("ended_at"),
  promptXml: text("prompt_xml").notNull(),
  stdout: text("stdout").notNull().default(""),
  stderr: text("stderr").notNull().default(""),
  outputJson: text("output_json", { mode: "json" }),
  tokensUsed: integer("tokens_used").notNull().default(0),
  costUsd: real("cost_usd").notNull().default(0),
  durationMs: integer("duration_ms").notNull().default(0),
  status: text("status").notNull(),
  errorMessage: text("error_message"),
});
