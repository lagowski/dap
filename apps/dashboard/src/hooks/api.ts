"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import type {
  AgentCreate,
  AgentDryRunRequest,
  AgentExport,
  AgentUpdate,
  PipelineCreate,
  PipelineExport,
  PipelineUiMetadataPatch,
  PipelineUpdate,
  ProjectCreate,
  ProjectRunRequest,
  ProjectUpdate,
  ValidateEnvRequest,
} from "@/lib/api/types";
import {
  createEntityQuery,
  createInvalidatingMutation,
  refetchWhile,
} from "@/lib/query-factory";
import { queryKeys } from "./api/query-keys";

export { queryKeys } from "./api/query-keys";
export {
  useAbortRun,
  useApproveGate,
  useDeleteRun,
  useGateCountdown,
  usePauseRun,
  usePipelineVersions,
  useResumeRun,
  useRun,
  useRunNodeExplain,
  useRunNodeLog,
  useRunNodeLogs,
  useRunsList,
  useRunStateHistory,
  useTriggerRun,
} from "./api/runs";

// ---------------------------------------------------------------------------
// Pipelines
// ---------------------------------------------------------------------------

export const usePipeline = createEntityQuery({
  scope: "pipelines",
  queryKey: queryKeys.pipeline,
  queryFn: api.getPipeline,
});

export const usePipelineUsage = createEntityQuery({
  scope: "pipelines",
  suffix: ["usage"],
  queryKey: queryKeys.pipelineUsage,
  queryFn: api.getPipelineUsage,
});

/**
 * Pre-run readiness of a pipeline's python-func callables (#710). Lazily
 * enabled (e.g. when the run dialog opens) so we don't resolve callables on
 * every pipeline list render.
 */
export const usePipelineReadiness = createEntityQuery({
  scope: "pipelines",
  suffix: ["readiness"],
  queryKey: queryKeys.pipelineReadiness,
  queryFn: api.getPipelineReadiness,
});

export function usePipelinesList() {
  return useQuery({
    queryKey: queryKeys.pipelinesList,
    queryFn: () => api.listPipelines(),
  });
}

export const useCreatePipeline = createInvalidatingMutation({
  mutationFn: (payload: PipelineCreate) => api.createPipeline(payload),
  invalidates: () => [queryKeys.pipelines],
});

export const useImportPipeline = createInvalidatingMutation({
  mutationFn: (payload: PipelineExport) => api.importPipeline(payload),
  invalidates: () => [queryKeys.pipelines],
});

export function useInspectPipelineImportBackends() {
  return useMutation({
    mutationFn: (payload: PipelineExport) => api.inspectPipelineImportBackends(payload),
  });
}

export const useUpdatePipeline = createInvalidatingMutation({
  mutationFn: ({ id, payload }: { id: string; payload: PipelineUpdate }) =>
    api.updatePipeline(id, payload),
  invalidates: ({ id }) => [queryKeys.pipelines, queryKeys.pipeline(id)],
});

export const useUpdatePipelineUiMetadata = createInvalidatingMutation({
  mutationFn: ({ id, payload }: { id: string; payload: PipelineUiMetadataPatch }) =>
    api.updatePipelineUiMetadata(id, payload),
  invalidates: ({ id }) => [
    queryKeys.pipeline(id),
    queryKeys.pipelineVersions(id),
  ],
});

export const useArchivePipeline = createInvalidatingMutation({
  mutationFn: (id: string) => api.archivePipeline(id),
  // Pipelines reference agents — archiving a pipeline drops its agents'
  // usage counts, so the /agents list cache is stale too.
  invalidates: (id) => [
    queryKeys.pipelines,
    queryKeys.pipeline(id),
    queryKeys.agents,
  ],
});

export function useValidatePipeline() {
  return useMutation({
    mutationFn: (payload: PipelineCreate) => api.validatePipeline(payload),
  });
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export function useAgentsList(filters?: { role?: string }) {
  return useQuery({
    queryKey: queryKeys.agentsList(filters),
    queryFn: () => api.listAgents(filters),
  });
}

export const useCreateAgent = createInvalidatingMutation({
  mutationFn: (payload: AgentCreate) => api.createAgent(payload),
  invalidates: () => [queryKeys.agents],
});

export const useAgent = createEntityQuery({
  scope: "agents",
  queryKey: queryKeys.agent,
  queryFn: api.getAgent,
});

export const useAgentUsage = createEntityQuery({
  scope: "agents",
  suffix: ["usage"],
  queryKey: queryKeys.agentUsage,
  queryFn: api.getAgentUsage,
});

/**
 * A python-func agent's callable docstring ("what this does", #747). Lazily
 * enabled — only fetched for managed agents where it's meaningful.
 */
export const useAgentCallableInfo = createEntityQuery({
  scope: "agents",
  suffix: ["callable-info"],
  queryKey: queryKeys.agentCallableInfo,
  queryFn: api.getAgentCallableInfo,
});

export const useAgentExecutions = createEntityQuery({
  scope: "agents",
  suffix: ["executions"],
  queryKey: queryKeys.agentExecutions,
  queryFn: api.getAgentExecutions,
});

export const useAgentVersions = createEntityQuery({
  scope: "agents",
  suffix: ["versions"],
  queryKey: queryKeys.agentVersions,
  queryFn: api.listAgentVersions,
});

export const useUpdateAgent = createInvalidatingMutation({
  mutationFn: ({ id, payload }: { id: string; payload: AgentUpdate }) =>
    api.updateAgent(id, payload),
  invalidates: ({ id }) => [
    queryKeys.agents,
    queryKeys.agent(id),
    queryKeys.agentVersions(id),
  ],
});

export const useArchiveAgent = createInvalidatingMutation({
  mutationFn: (id: string) => api.archiveAgent(id),
  invalidates: (id) => [queryKeys.agents, queryKeys.agent(id)],
});

export const useImportAgent = createInvalidatingMutation({
  mutationFn: (payload: AgentExport) => api.importAgent(payload),
  invalidates: () => [queryKeys.agents],
});

export function useDryRunAgent() {
  // Stateless on the server (#103) — no cache invalidation. The mutation
  // is here purely for the loading/error/result UI on the Test panel.
  return useMutation({
    mutationFn: (payload: AgentDryRunRequest) => api.dryRunAgent(payload),
  });
}

// ---------------------------------------------------------------------------
// Projects (v0.6 / #67)
// ---------------------------------------------------------------------------

export function useProjectsList(filters?: { archived?: boolean }) {
  return useQuery({
    queryKey: queryKeys.projectsList(filters),
    queryFn: () => api.listProjects(filters),
  });
}

export const useProject = createEntityQuery({
  scope: "projects",
  queryKey: queryKeys.project,
  queryFn: api.getProject,
});

export const useCreateProject = createInvalidatingMutation({
  mutationFn: (payload: ProjectCreate) => api.createProject(payload),
  invalidates: () => [queryKeys.projects],
});

export const useUpdateProject = createInvalidatingMutation({
  mutationFn: ({ id, payload }: { id: string; payload: ProjectUpdate }) =>
    api.updateProject(id, payload),
  invalidates: ({ id }) => [queryKeys.projects, queryKeys.project(id)],
});

export const useArchiveProject = createInvalidatingMutation({
  mutationFn: (id: string) => api.archiveProject(id),
  invalidates: (id) => [queryKeys.projects, queryKeys.project(id)],
});

export function useValidateProjectEnv() {
  return useMutation({
    mutationFn: (payload: ValidateEnvRequest) => api.validateProjectEnv(payload),
  });
}

export const useTriggerProjectRun = createInvalidatingMutation({
  mutationFn: (params: {
    id: string;
    kind: string;
    payload?: ProjectRunRequest;
  }) => api.triggerProjectRun(params.id, params.kind, params.payload),
  invalidates: () => [queryKeys.runs],
});

export function useWorkspaceStatus(projectId: string | null) {
  return useQuery({
    queryKey: ["projects", projectId, "workspace"],
    queryFn: () =>
      api.request<{
        exists: boolean;
        path: string | null;
        branch: string | null;
        clean: boolean | null;
        last_commit: string | null;
      }>(`/projects/${encodeURIComponent(projectId!)}/workspace/status`),
    enabled: projectId != null,
    // Poll until the workspace exists (init runs in the background).
    refetchInterval: refetchWhile((d) => !d.exists, 10_000),
  });
}

export function useInitWorkspace(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.request<{ exists: boolean; path: string; branch: string; initialized: boolean }>(
        `/projects/${encodeURIComponent(projectId)}/workspace/init`,
        { method: "POST" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects", projectId, "workspace"] });
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

export function useSyncWorkspace(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.request<{ exists: boolean; path: string; branch: string }>(
        `/projects/${encodeURIComponent(projectId)}/workspace/sync`,
        { method: "POST" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects", projectId, "workspace"] });
    },
  });
}

export function useProjectIssues(projectId: string | null) {
  return useQuery({
    queryKey: ["projects", projectId, "issues"],
    queryFn: () => api.listProjectIssues(projectId!),
    enabled: projectId != null,
    staleTime: 60_000,
  });
}

export function useSettings() {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => api.getSettings(),
  });
}

// ---------------------------------------------------------------------------
// Auth (Phase B, #300)
// ---------------------------------------------------------------------------

import type { LoginCredentials, RegisterCredentials } from "@/lib/api/types";

/**
 * Current user, or ``null`` when the cookie is missing / stale.
 *
 * Stays cached across navigations — every page that renders chrome
 * dependent on the user (sidebar, header, conditional admin links)
 * shares the single result. ``staleTime`` is long because the
 * underlying row only changes when the user explicitly logs out or
 * an admin promotes them; we'd rather show stale ``is_superuser``
 * for a few minutes than re-fetch on every navigation.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: queryKeys.currentUser,
    queryFn: () => api.getCurrentUser(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"] as const,
    queryFn: () => api.getHealth(),
    // /health is cheap (single SELECT 1) but no point hammering it —
    // the login page only needs the value once on mount, and any
    // page mounted later wants a fresh probe rather than a 5-minute
    // stale snapshot of "Database unreachable".
    staleTime: 30 * 1000,
    retry: false,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (creds: LoginCredentials) => api.login(creds),
    onSuccess: () => {
      // Force a re-fetch — the cookie is now live and ``useCurrentUser``
      // should return the real user instead of ``null``. Also bust
      // everything else: any data the previous (anonymous / 401)
      // session might have cached is suspect.
      qc.invalidateQueries();
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.logout(),
    onSuccess: () => {
      qc.setQueryData(queryKeys.currentUser, null);
      qc.removeQueries();
    },
  });
}

export function useForgotPassword() {
  // No invalidation — the user has no session, so there's nothing
  // to refresh.
  return useMutation({
    mutationFn: (email: string) => api.forgotPassword(email),
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: (params: { token: string; password: string }) =>
      api.resetPassword(params.token, params.password),
  });
}

export function useUpdateMyPassword() {
  return useMutation({
    mutationFn: (password: string) => api.updateMyPassword(password),
  });
}

export function useRegister() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (creds: RegisterCredentials) => api.register(creds),
    onSuccess: () => {
      // Auto-login already minted the cookie; flush caches so the
      // new (authenticated) identity drives every subsequent fetch.
      qc.invalidateQueries();
    },
  });
}

// ---------------------------------------------------------------------------
// Admin — Users (#301, sub-C2)
// ---------------------------------------------------------------------------

import type { AdminUserUpdate } from "@/lib/api/types";

/**
 * Admin users list. Filter param exposed so the page can flip the
 * "include soft-deleted" toggle without re-keying the query manually.
 */
export function useAdminUsers(filters: { includeDeleted?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.adminUsersList(filters),
    queryFn: () => api.listAdminUsers(filters),
  });
}

export const useUpdateAdminUser = createInvalidatingMutation({
  mutationFn: (params: { id: string; payload: AdminUserUpdate }) =>
    api.updateAdminUser(params.id, params.payload),
  // Every variant of the admin-users list re-fetches; if an admin
  // demotes themself the currentUser badge needs to flip too.
  invalidates: () => [queryKeys.adminUsers, queryKeys.currentUser],
});

export const useDeleteAdminUser = createInvalidatingMutation({
  mutationFn: (id: string) => api.deleteAdminUser(id),
  invalidates: () => [queryKeys.adminUsers],
});

// ---------------------------------------------------------------------------
// Admin — Audit log (#301, sub-C3)
// ---------------------------------------------------------------------------

export function useAuditEvents(
  filters: {
    eventType?: string;
    eventTypePrefix?: string;
    userId?: string;
    createdFrom?: string;
    createdTo?: string;
    offset?: number;
    limit?: number;
  } = {},
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: queryKeys.adminAuditEventsList(filters),
    queryFn: () => api.listAuditEvents(filters),
    // ``enabled`` lets the audit-log page pause the query while the
    // user is mid-typing a UUID into the filter input — otherwise
    // every keystroke would fire a 422 against the engine
    // (Copilot review on PR #329).
    enabled: options.enabled ?? true,
  });
}

// ---------------------------------------------------------------------------
// Admin — API tokens (#301, sub-C4)
// ---------------------------------------------------------------------------

export function useAdminApiTokens(filters: {
  includeRevoked?: boolean;
  offset?: number;
  limit?: number;
} = {}) {
  return useQuery({
    queryKey: queryKeys.adminApiTokensList(filters),
    queryFn: () => api.listAdminApiTokens(filters),
  });
}

export const useAdminRevokeApiToken = createInvalidatingMutation({
  mutationFn: (id: string) => api.adminRevokeApiToken(id),
  invalidates: () => [queryKeys.adminApiTokens],
});

export const useCreateApiToken = createInvalidatingMutation({
  mutationFn: (payload: { name: string; expires_in_days?: number | null }) =>
    api.createApiToken(payload),
  invalidates: () => [queryKeys.adminApiTokens],
});

// ---------------------------------------------------------------------------
// Admin — Instance settings (#301, sub-C5)
// ---------------------------------------------------------------------------

/**
 * Instance-wide settings snapshot. Long-cached (5 min) — instance
 * config is env-var driven, so the value only changes on engine
 * restart. The admin page accepts that staleness for a smoother
 * navigation experience.
 */
export function useAdminSettings() {
  return useQuery({
    queryKey: queryKeys.adminSettings,
    queryFn: () => api.getAdminSettings(),
    staleTime: 5 * 60 * 1000,
  });
}

/** Instance env vars — admin-only, masked previews (#388). */
export function useInstanceEnvVars() {
  return useQuery({
    queryKey: queryKeys.instanceEnvVars,
    queryFn: () => api.listInstanceEnvVars(),
  });
}

export const useUpsertInstanceEnvVar = createInvalidatingMutation({
  mutationFn: ({ key, value }: { key: string; value: string }) =>
    api.upsertInstanceEnvVar(key, value),
  invalidates: () => [queryKeys.instanceEnvVars],
});

export const useDeleteInstanceEnvVar = createInvalidatingMutation({
  mutationFn: (key: string) => api.deleteInstanceEnvVar(key),
  invalidates: () => [queryKeys.instanceEnvVars],
});

/**
 * Config assistant chat (#689). A plain mutation — the assistant panel owns
 * the message history and sends the full transcript each turn.
 */
export function useAssistantChat() {
  return useMutation({
    mutationFn: (vars: {
      messages: import("@/lib/api/types").AssistantMessage[];
      context?: Record<string, unknown>;
    }) => api.assistantChat(vars.messages, vars.context),
  });
}
