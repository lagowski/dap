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
import { queryKeys } from "./api/query-keys";

export { queryKeys } from "./api/query-keys";
export {
  useAbortRun,
  useApproveGate,
  useGateCountdown,
  usePauseRun,
  usePipelineVersions,
  useResumeRun,
  useRun,
  useRunNodeLog,
  useRunNodeLogs,
  useRunsList,
  useRunStateHistory,
  useTriggerRun,
} from "./api/runs";

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

export function useInspectPipelineImportBackends() {
  return useMutation({
    mutationFn: (payload: PipelineExport) => api.inspectPipelineImportBackends(payload),
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

export function useUpdatePipelineUiMetadata() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: PipelineUiMetadataPatch }) =>
      api.updatePipelineUiMetadata(id, payload),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.pipeline(variables.id) });
      qc.invalidateQueries({ queryKey: queryKeys.pipelineVersions(variables.id) });
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

export function useAgentUsage(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.agentUsage(id) : ["agents", "noop", "usage"],
    queryFn: () =>
      id ? api.getAgentUsage(id) : Promise.reject(new Error("no id")),
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

export function useValidateProjectEnv() {
  return useMutation({
    mutationFn: (payload: ValidateEnvRequest) => api.validateProjectEnv(payload),
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
    refetchInterval: (query) => {
      const d = query.state.data;
      return d?.exists ? false : 10_000;
    },
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

// ---------------------------------------------------------------------------
// Admin — Audit log (#301, sub-C3)
// ---------------------------------------------------------------------------

export function useAuditEvents(
  filters: {
    eventType?: string;
    userId?: string;
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

export function useAdminRevokeApiToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.adminRevokeApiToken(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.adminApiTokens });
    },
  });
}

export function useCreateApiToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { name: string; expires_in_days?: number | null }) =>
      api.createApiToken(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.adminApiTokens });
    },
  });
}

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
