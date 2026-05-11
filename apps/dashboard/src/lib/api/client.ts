/**
 * Typed fetch client for the DAP engine API.
 *
 * All endpoints throw `ApiError` on non-2xx responses; callers handle via
 * TanStack Query's error state.
 */

import type {
  Agent,
  AgentCreate,
  AgentDryRunRequest,
  AgentDryRunResponse,
  AgentExport,
  AgentUpdate,
  CurrentUser,
  LoginCredentials,
  NodeExecutionLog,
  PaginatedList,
  Pipeline,
  PipelineCreate,
  PipelineExport,
  PipelineState,
  PipelineUpdate,
  Project,
  ProjectCreate,
  ProjectRunRequest,
  ProjectUpdate,
  RegisterCredentials,
  Run,
  RunCreateRequest,
  SettingsView,
  StateSnapshot,
  ValidationResult,
} from "./types";

/**
 * After Phase B1 (#300) the browser talks to the dashboard's
 * ``/api/*`` proxy, never the engine directly. The proxy reads the
 * JWT from an httpOnly cookie and forwards it as a Bearer header.
 *
 * Server-side fetches (Next route handlers, server components) still
 * hit the engine directly via ``DAP_ENGINE_URL`` — but those paths
 * use a different client (``lib/auth/engine.ts``). This client is
 * browser-only.
 */
const API_BASE_URL = "/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: unknown,
    message?: string,
  ) {
    super(message ?? `API error ${status}`);
    this.name = "ApiError";
  }
}

/**
 * Extract a human-readable message from an arbitrary error.
 *
 * Handles ApiError shapes: FastAPI `{detail: string}` (HTTPException),
 * FastAPI `{detail: [{msg}, ...]}` (422 validation), plain string `detail`
 * (non-JSON response → status text), and generic `{message}` payloads
 * (proxies / non-FastAPI servers). Falls back to Error.message otherwise.
 */
export function formatApiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (typeof error.detail === "string") return error.detail;

    const payload =
      error.detail && typeof error.detail === "object"
        ? (error.detail as { detail?: unknown; message?: unknown })
        : null;
    const detail = payload?.detail;

    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return detail.map((d) => (d as { msg?: string })?.msg ?? String(d)).join("; ");
    }
    // Structured FastAPI detail: ``HTTPException(detail={"message": "...", ...})``
    // wraps as ``{"detail": {"message": "..."}}`` on the wire. The 409 from
    // archive_agent uses this shape.
    if (detail && typeof detail === "object") {
      const inner = (detail as { message?: unknown }).message;
      if (typeof inner === "string") return inner;
    }
    if (typeof payload?.message === "string") return payload.message;
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

async function request<T>(
  path: string,
  init?: RequestInit & { json?: unknown; skipAuthRedirect?: boolean },
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };

  let body: BodyInit | undefined = init?.body as BodyInit | undefined;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    body,
    // Cookies attach automatically since the dashboard's ``/api/*``
    // proxy lives on the same origin, but pass ``same-origin``
    // explicitly so the contract is documented in code.
    credentials: "same-origin",
  });

  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = await response.json();
    } catch {
      // not JSON
    }
    // 401 → the cookie is missing / stale. Tell the browser to land
    // on /login with a ``next=`` hint so the user comes back to
    // wherever they were. Probing calls (``getCurrentUser``) opt
    // out via ``skipAuthRedirect`` — they need to *see* the 401
    // to decide whether to render the signed-in UI or the sign-in
    // entry point. Server-side fetches (no ``window``) just
    // propagate the error so the caller can decide.
    if (
      response.status === 401 &&
      typeof window !== "undefined" &&
      !init?.skipAuthRedirect
    ) {
      const next = window.location.pathname + window.location.search;
      window.location.replace(`/login?next=${encodeURIComponent(next)}`);
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export async function listRuns(params: {
  pipelineId?: string;
  finalStatus?: string;
  /**
   * Project scope (#64 / #68). Pass a project id to scope the list,
   * the literal string ``"null"`` for ad-hoc-only (runs without
   * project_id), or omit for org-wide.
   */
  projectId?: string;
  offset?: number;
  limit?: number;
} = {}): Promise<PaginatedList<Run>> {
  const search = new URLSearchParams();
  if (params.pipelineId) search.set("pipeline_id", params.pipelineId);
  if (params.finalStatus) search.set("final_status", params.finalStatus);
  if (params.projectId) search.set("project_id", params.projectId);
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  const qs = search.toString();
  return request<PaginatedList<Run>>(`/runs${qs ? `?${qs}` : ""}`);
}

export async function getRun(id: string): Promise<Run> {
  return request<Run>(`/runs/${encodeURIComponent(id)}`);
}

export async function getRunState(id: string): Promise<PipelineState> {
  return request<PipelineState>(`/runs/${encodeURIComponent(id)}/state`);
}

export async function getRunStateHistory(id: string): Promise<StateSnapshot[]> {
  return request<StateSnapshot[]>(`/runs/${encodeURIComponent(id)}/state/history`);
}

export async function getRunNodeLog(
  runId: string,
  nodeId: string,
): Promise<NodeExecutionLog> {
  return request<NodeExecutionLog>(
    `/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}`,
  );
}

export async function triggerRun(payload: RunCreateRequest): Promise<Run> {
  return request<Run>("/runs", { method: "POST", json: payload });
}

export async function listPipelineVersions(id: string): Promise<Pipeline[]> {
  return request<Pipeline[]>(`/pipelines/${encodeURIComponent(id)}/versions`);
}

export async function abortRun(id: string): Promise<Run> {
  return request<Run>(`/runs/${encodeURIComponent(id)}/abort`, { method: "POST" });
}

export async function pauseRun(id: string): Promise<Run> {
  return request<Run>(`/runs/${encodeURIComponent(id)}/pause`, { method: "POST" });
}

export async function resumeRun(id: string): Promise<Run> {
  return request<Run>(`/runs/${encodeURIComponent(id)}/resume`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// Pipelines
// ---------------------------------------------------------------------------

export async function listPipelines(): Promise<PaginatedList<Pipeline>> {
  return request<PaginatedList<Pipeline>>("/pipelines");
}

export async function getPipeline(id: string): Promise<Pipeline> {
  return request<Pipeline>(`/pipelines/${encodeURIComponent(id)}`);
}

export async function getPipelineVersion(
  id: string,
  version: number,
): Promise<Pipeline> {
  return request<Pipeline>(
    `/pipelines/${encodeURIComponent(id)}/versions/${version}`,
  );
}

export async function createPipeline(payload: PipelineCreate): Promise<Pipeline> {
  return request<Pipeline>("/pipelines", { method: "POST", json: payload });
}

export async function updatePipeline(
  id: string,
  payload: PipelineUpdate,
): Promise<Pipeline> {
  return request<Pipeline>(`/pipelines/${encodeURIComponent(id)}`, {
    method: "PUT",
    json: payload,
  });
}

export async function validatePipeline(
  payload: PipelineCreate,
): Promise<ValidationResult> {
  return request<ValidationResult>("/pipelines/validate", {
    method: "POST",
    json: payload,
  });
}

export async function exportPipeline(
  id: string,
  options: { bundle?: boolean } = {},
): Promise<PipelineExport> {
  // Default ``bundle=false`` keeps the wire-compatible path with #124;
  // callers opt into the bigger envelope explicitly.
  const search = new URLSearchParams();
  if (options.bundle) search.set("bundle", "true");
  const qs = search.toString();
  return request<PipelineExport>(
    `/pipelines/${encodeURIComponent(id)}/export${qs ? `?${qs}` : ""}`,
  );
}

export async function importPipeline(payload: PipelineExport): Promise<Pipeline> {
  return request<Pipeline>("/pipelines/import", { method: "POST", json: payload });
}

export async function archivePipeline(id: string): Promise<void> {
  await request<void>(`/pipelines/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export async function listAgents(params: {
  role?: string;
  archived?: boolean;
  limit?: number;
} = {}): Promise<PaginatedList<Agent>> {
  const search = new URLSearchParams();
  if (params.role) search.set("role", params.role);
  if (params.archived !== undefined) search.set("archived", String(params.archived));
  // Default to 500 so bundled pipeline agents (13+ per import) are always
  // included — the default engine page size of 50 causes "Agent not found"
  // in the inspector when >50 agents exist (#228).
  search.set("limit", String(params.limit ?? 500));
  const qs = search.toString();
  return request<PaginatedList<Agent>>(`/agents${qs ? `?${qs}` : ""}`);
}

export async function getAgent(id: string): Promise<Agent> {
  return request<Agent>(`/agents/${encodeURIComponent(id)}`);
}

export async function createAgent(payload: AgentCreate): Promise<Agent> {
  return request<Agent>("/agents", { method: "POST", json: payload });
}

export async function updateAgent(id: string, payload: AgentUpdate): Promise<Agent> {
  return request<Agent>(`/agents/${encodeURIComponent(id)}`, {
    method: "PUT",
    json: payload,
  });
}

export async function archiveAgent(id: string): Promise<void> {
  await request<void>(`/agents/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function listAgentVersions(id: string): Promise<Agent[]> {
  return request<Agent[]>(`/agents/${encodeURIComponent(id)}/versions`);
}

export async function exportAgent(id: string): Promise<AgentExport> {
  return request<AgentExport>(`/agents/${encodeURIComponent(id)}/export`);
}

export async function importAgent(payload: AgentExport): Promise<Agent> {
  return request<Agent>("/agents/import", { method: "POST", json: payload });
}

export async function dryRunAgent(
  payload: AgentDryRunRequest,
): Promise<AgentDryRunResponse> {
  return request<AgentDryRunResponse>("/agents/dry-run", {
    method: "POST",
    json: payload,
  });
}

// ---------------------------------------------------------------------------
// Projects (v0.6)
// ---------------------------------------------------------------------------

export async function listProjects(params?: {
  archived?: boolean;
}): Promise<PaginatedList<Project>> {
  const qs = new URLSearchParams();
  // Always forward ``archived`` when supplied so the request matches
  // the React Query key ``projectsList(filters)`` exactly. Skipping
  // it when ``archived === false`` produced two cache entries (default
  // call vs. ``{ archived: false }``) for the same server response.
  if (params?.archived !== undefined) {
    qs.set("archived", String(params.archived));
  }
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<PaginatedList<Project>>(`/projects${suffix}`);
}

export async function getProject(id: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(id)}`);
}

export async function createProject(payload: ProjectCreate): Promise<Project> {
  return request<Project>("/projects", { method: "POST", json: payload });
}

export async function updateProject(
  id: string,
  payload: ProjectUpdate,
): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(id)}`, {
    method: "PUT",
    json: payload,
  });
}

export async function archiveProject(id: string): Promise<void> {
  await request<void>(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function triggerProjectRun(
  id: string,
  kind: string,
  payload?: ProjectRunRequest,
): Promise<Run> {
  return request<Run>(
    `/projects/${encodeURIComponent(id)}/run/${encodeURIComponent(kind)}`,
    { method: "POST", json: payload ?? {} },
  );
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

export async function getSettings(): Promise<SettingsView> {
  return request<SettingsView>("/settings");
}

// ---------------------------------------------------------------------------
// Auth (Phase B, #300)
// ---------------------------------------------------------------------------

/**
 * Auth endpoints target the dashboard's own ``/api/auth/*`` route
 * handlers, not the engine directly. The handlers proxy to the
 * engine and own the cookie lifecycle (set on login, clear on
 * logout). ``request`` already prepends ``/api`` for us, so the
 * paths here are ``/auth/*``.
 */

export async function login(creds: LoginCredentials): Promise<void> {
  await request<{ ok: true }>("/auth/login", { method: "POST", json: creds });
}

export async function logout(): Promise<void> {
  await request<{ ok: true }>("/auth/logout", { method: "POST" });
}

export async function register(creds: RegisterCredentials): Promise<void> {
  await request<{ ok: true; verified: boolean }>("/auth/register", {
    method: "POST",
    json: creds,
  });
}

/**
 * ``GET /api/auth/me`` returns the current user or 401 if the cookie
 * is missing / invalid. We translate the 401 to ``null`` here so
 * call sites can use the result as "is logged in?" without dealing
 * with thrown ApiErrors for the most common case.
 */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  try {
    return await request<CurrentUser>("/auth/me", { skipAuthRedirect: true });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null;
    }
    throw error;
  }
}
