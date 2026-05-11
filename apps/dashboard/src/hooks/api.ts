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
  PipelineUpdate,
  ProjectCreate,
  ProjectRunRequest,
  ProjectUpdate,
  RunCreateRequest,
} from "@/lib/api/types";

export const queryKeys = {
  runs: ["runs"] as const,
  runsList: (filters?: {
    pipelineId?: string;
    finalStatus?: string;
    projectId?: string;
  }) => ["runs", "list", filters ?? {}] as const,
  run: (id: string) => ["runs", id] as const,
  runState: (id: string) => ["runs", id, "state"] as const,
  runHistory: (id: string) => ["runs", id, "history"] as const,
  runNodeLog: (runId: string, nodeId: string) =>
    ["runs", runId, "nodes", nodeId] as const,
  agents: ["agents"] as const,
  agentsList: (filters?: { role?: string }) => ["agents", "list", filters ?? {}] as const,
  agent: (id: string) => ["agents", id] as const,
  agentVersions: (id: string) => ["agents", id, "versions"] as const,
  pipelines: ["pipelines"] as const,
  pipelinesList: ["pipelines", "list"] as const,
  pipeline: (id: string) => ["pipelines", id] as const,
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
};

const RUNS_LIST_REFETCH_MS = 2_000;
const RUN_DETAIL_REFETCH_MS = 2_000;

export function useRunsList(
  filters?: {
    pipelineId?: string;
    finalStatus?: string;
    projectId?: string;
  },
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: queryKeys.runsList(filters),
    queryFn: () => api.listRuns(filters),
    enabled: options?.enabled ?? true,
    refetchInterval: (query) => {
      // Stop polling when there are no running runs
      const data = query.state.data;
      if (data && !data.items.some((r) => r.final_status === "running")) {
        return false;
      }
      return RUNS_LIST_REFETCH_MS;
    },
  });
}

export function useRun(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.run(id) : ["runs", "noop"],
    queryFn: () => (id ? api.getRun(id) : Promise.reject(new Error("no id"))),
    enabled: id != null,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && data.final_status !== "running") return false;
      return RUN_DETAIL_REFETCH_MS;
    },
  });
}

export function useRunStateHistory(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.runHistory(id) : ["runs", "noop", "history"],
    queryFn: () =>
      id ? api.getRunStateHistory(id) : Promise.reject(new Error("no id")),
    enabled: id != null,
  });
}

export function useRunNodeLog(runId: string | null, nodeId: string | null) {
  return useQuery({
    queryKey:
      runId && nodeId
        ? queryKeys.runNodeLog(runId, nodeId)
        : ["runs", "noop", "nodes", "noop"],
    queryFn: () =>
      runId && nodeId
        ? api.getRunNodeLog(runId, nodeId)
        : Promise.reject(new Error("no id")),
    enabled: runId != null && nodeId != null,
  });
}

export function usePipeline(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.pipeline(id) : ["pipelines", "noop"],
    queryFn: () => (id ? api.getPipeline(id) : Promise.reject(new Error("no id"))),
    enabled: id != null,
  });
}

export function usePipelinesList() {
  return useQuery({
    queryKey: queryKeys.pipelinesList,
    queryFn: () => api.listPipelines(),
  });
}

export function useCreatePipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PipelineCreate) => api.createPipeline(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines });
    },
  });
}

export function useImportPipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PipelineExport) => api.importPipeline(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines });
    },
  });
}

export function useUpdatePipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: PipelineUpdate }) =>
      api.updatePipeline(id, payload),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines });
      qc.invalidateQueries({ queryKey: queryKeys.pipeline(variables.id) });
    },
  });
}

export function useArchivePipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.archivePipeline(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines });
      qc.invalidateQueries({ queryKey: queryKeys.pipeline(id) });
      // Pipelines reference agents — archiving a pipeline drops its
      // agents' usage counts, so the /agents list cache is now stale.
      qc.invalidateQueries({ queryKey: queryKeys.agents });
    },
  });
}

export function useValidatePipeline() {
  return useMutation({
    mutationFn: (payload: PipelineCreate) => api.validatePipeline(payload),
  });
}

export function useAgentsList(filters?: { role?: string }) {
  return useQuery({
    queryKey: queryKeys.agentsList(filters),
    queryFn: () => api.listAgents(filters),
  });
}

export function useCreateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AgentCreate) => api.createAgent(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.agents });
    },
  });
}

export function useAgent(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.agent(id) : ["agents", "noop"],
    queryFn: () => (id ? api.getAgent(id) : Promise.reject(new Error("no id"))),
    enabled: id != null,
  });
}

export function useAgentVersions(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.agentVersions(id) : ["agents", "noop", "versions"],
    queryFn: () =>
      id ? api.listAgentVersions(id) : Promise.reject(new Error("no id")),
    enabled: id != null,
  });
}

export function useUpdateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: AgentUpdate }) =>
      api.updateAgent(id, payload),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.agents });
      qc.invalidateQueries({ queryKey: queryKeys.agent(variables.id) });
      qc.invalidateQueries({ queryKey: queryKeys.agentVersions(variables.id) });
    },
  });
}

export function useArchiveAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.archiveAgent(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: queryKeys.agents });
      qc.invalidateQueries({ queryKey: queryKeys.agent(id) });
    },
  });
}

export function useImportAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AgentExport) => api.importAgent(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.agents });
    },
  });
}

export function useDryRunAgent() {
  // Stateless on the server (#103) — no cache invalidation. The mutation
  // is here purely for the loading/error/result UI on the Test panel.
  return useMutation({
    mutationFn: (payload: AgentDryRunRequest) => api.dryRunAgent(payload),
  });
}

function useRunActionMutation<T>(action: (id: string) => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: action,
    onSuccess: (_data, runId) => {
      qc.invalidateQueries({ queryKey: queryKeys.run(runId) });
      qc.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });
}

export function useAbortRun() {
  return useRunActionMutation(api.abortRun);
}

export function usePauseRun() {
  return useRunActionMutation(api.pauseRun);
}

export function useResumeRun() {
  return useRunActionMutation(api.resumeRun);
}

export function usePipelineVersions(
  id: string | null,
  options?: { enabled?: boolean },
) {
  const enabled = (options?.enabled ?? true) && id != null;
  return useQuery({
    queryKey: id ? queryKeys.pipelineVersions(id) : ["pipelines", "noop", "versions"],
    queryFn: () =>
      id ? api.listPipelineVersions(id) : Promise.reject(new Error("no id")),
    enabled,
  });
}

export function useTriggerRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: RunCreateRequest) => api.triggerRun(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.runs });
    },
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

export function useProject(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.project(id) : ["projects", "noop"],
    queryFn: () => (id ? api.getProject(id) : Promise.reject(new Error("no id"))),
    enabled: id != null,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ProjectCreate) => api.createProject(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.projects });
    },
  });
}

export function useUpdateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ProjectUpdate }) =>
      api.updateProject(id, payload),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.projects });
      qc.invalidateQueries({ queryKey: queryKeys.project(variables.id) });
    },
  });
}

export function useArchiveProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.archiveProject(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: queryKeys.projects });
      qc.invalidateQueries({ queryKey: queryKeys.project(id) });
    },
  });
}

export function useTriggerProjectRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      id: string;
      kind: string;
      payload?: ProjectRunRequest;
    }) => api.triggerProjectRun(params.id, params.kind, params.payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.runs });
    },
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

export function useUpdateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: { id: string; payload: AdminUserUpdate }) =>
      api.updateAdminUser(params.id, params.payload),
    onSuccess: () => {
      // Every variant of the admin-users list re-fetches; if an admin
      // demotes themself the currentUser badge needs to flip too.
      qc.invalidateQueries({ queryKey: queryKeys.adminUsers });
      qc.invalidateQueries({ queryKey: queryKeys.currentUser });
    },
  });
}

export function useDeleteAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteAdminUser(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.adminUsers });
    },
  });
}
