/**
 * Typed fetch client for the DAP engine API.
 *
 * All endpoints throw `ApiError` on non-2xx responses; callers handle via
 * TanStack Query's error state.
 */

import type {
  Agent,
  AgentCreate,
  AgentUpdate,
  NodeExecutionLog,
  PaginatedList,
  Pipeline,
  PipelineCreate,
  PipelineState,
  PipelineUpdate,
  Run,
  RunCreateRequest,
  SettingsView,
  StateSnapshot,
  ValidationResult,
} from "./types";

const ENGINE_URL =
  process.env.NEXT_PUBLIC_DAP_ENGINE_URL ?? "http://127.0.0.1:7333";

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
    if (typeof payload?.message === "string") return payload.message;
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

async function request<T>(
  path: string,
  init?: RequestInit & { json?: unknown },
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

  const response = await fetch(`${ENGINE_URL}${path}`, {
    ...init,
    headers,
    body,
  });

  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = await response.json();
    } catch {
      // not JSON
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
  offset?: number;
  limit?: number;
} = {}): Promise<PaginatedList<Run>> {
  const search = new URLSearchParams();
  if (params.pipelineId) search.set("pipeline_id", params.pipelineId);
  if (params.finalStatus) search.set("final_status", params.finalStatus);
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

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export async function listAgents(params: {
  role?: string;
  archived?: boolean;
} = {}): Promise<PaginatedList<Agent>> {
  const search = new URLSearchParams();
  if (params.role) search.set("role", params.role);
  if (params.archived !== undefined) search.set("archived", String(params.archived));
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

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

export async function getSettings(): Promise<SettingsView> {
  return request<SettingsView>("/settings");
}
