export const queryKeys = {
  runs: ["runs"] as const,
  runsList: (filters?: {
    pipelineId?: string;
    finalStatus?: string;
    statuses?: string[];
    from?: string;
    to?: string;
    projectId?: string;
  }) => ["runs", "list", filters ?? {}] as const,
  run: (id: string) => ["runs", id] as const,
  runState: (id: string) => ["runs", id, "state"] as const,
  runHistory: (id: string) => ["runs", id, "history"] as const,
  runNodeLogs: (runId: string) => ["runs", runId, "nodes"] as const,
  runNodeLog: (runId: string, nodeId: string) =>
    ["runs", runId, "nodes", nodeId] as const,
  runNodeExplain: (runId: string, nodeId: string) =>
    ["runs", runId, "nodes", nodeId, "explain"] as const,
  agents: ["agents"] as const,
  agentsList: (filters?: { role?: string }) => ["agents", "list", filters ?? {}] as const,
  agent: (id: string) => ["agents", id] as const,
  agentVersions: (id: string) => ["agents", id, "versions"] as const,
  agentUsage: (id: string) => ["agents", id, "usage"] as const,
  agentCallableInfo: (id: string) => ["agents", id, "callable-info"] as const,
  agentExecutions: (id: string) => ["agents", id, "executions"] as const,
  pipelines: ["pipelines"] as const,
  pipelinesList: ["pipelines", "list"] as const,
  pipeline: (id: string) => ["pipelines", id] as const,
  pipelineUsage: (id: string) => ["pipelines", id, "usage"] as const,
  pipelineReadiness: (id: string) => ["pipelines", id, "readiness"] as const,
  pipelineVersions: (id: string) => ["pipelines", id, "versions"] as const,
  projects: ["projects"] as const,
  projectsList: (filters?: { archived?: boolean }) =>
    ["projects", "list", filters ?? {}] as const,
  project: (id: string) => ["projects", id] as const,
  settings: ["settings"] as const,
  currentUser: ["auth", "me"] as const,
  adminUsers: ["admin", "users"] as const,
  adminUsersList: (filters?: { includeDeleted?: boolean }) =>
    ["admin", "users", "list", filters ?? {}] as const,
  adminAuditEvents: ["admin", "audit-events"] as const,
  adminAuditEventsList: (filters?: {
    eventType?: string;
    eventTypePrefix?: string;
    userId?: string;
    createdFrom?: string;
    createdTo?: string;
    offset?: number;
    limit?: number;
  }) => ["admin", "audit-events", "list", filters ?? {}] as const,
  adminApiTokens: ["admin", "api-tokens"] as const,
  adminApiTokensList: (filters?: {
    includeRevoked?: boolean;
    offset?: number;
    limit?: number;
  }) => ["admin", "api-tokens", "list", filters ?? {}] as const,
  adminSettings: ["admin", "settings"] as const,
  instanceEnvVars: ["admin", "env-vars"] as const,
};
