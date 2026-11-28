import type { APIRequestContext } from '@playwright/test';

export interface Run {
  id: string;
  pipeline_id: string;
  pipeline_version: number;
  project_id: string | null;
  final_status: 'running' | 'success' | 'failed' | 'paused' | 'aborted';
  trigger_source: string;
  started_at: string;
  ended_at: string | null;
  tokens_used: number;
  cost_usd: number;
}

// POST /runs is async-only: returns 201 immediately with final_status="running"
// and schedules a background task. There's no synchronous seeding path, so
// tests trigger a real run with a single-node bash agent (echo ok) and poll
// to a terminal state — typical wall time ~200-500ms on a warm engine.
export async function triggerRun(
  request: APIRequestContext,
  pipelineId: string,
): Promise<Run> {
  const response = await request.post('/api/runs', {
    data: { pipeline_id: pipelineId },
  });
  if (!response.ok()) {
    throw new Error(
      `Failed to trigger run (status ${response.status()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as Run;
}

export async function waitForRunCompletion(
  request: APIRequestContext,
  runId: string,
  timeoutMs = 15_000,
): Promise<Run> {
  return waitForRunStatus(
    request,
    runId,
    (status) => status !== 'running',
    timeoutMs,
  );
}

/**
 * Poll /api/runs/<id> until `predicate(final_status)` returns true.
 * Used by lifecycle specs that need specific transitions (e.g. wait
 * for "paused" or "aborted") rather than "anything but running".
 */
export async function waitForRunStatus(
  request: APIRequestContext,
  runId: string,
  predicate: (status: Run['final_status']) => boolean,
  timeoutMs = 15_000,
): Promise<Run> {
  const start = Date.now();
  let lastStatus: string | null = null;
  while (Date.now() - start < timeoutMs) {
    const response = await request.get(`/api/runs/${runId}`);
    if (response.ok()) {
      const run = (await response.json()) as Run;
      lastStatus = run.final_status;
      if (predicate(run.final_status)) return run;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `Run ${runId} did not match predicate within ${timeoutMs}ms (last status=${lastStatus})`,
  );
}

export async function pauseRun(request: APIRequestContext, runId: string): Promise<Run> {
  const response = await request.post(`/api/runs/${runId}/pause`);
  if (!response.ok()) {
    throw new Error(`pauseRun failed (${response.status()}): ${await response.text()}`);
  }
  return (await response.json()) as Run;
}

export async function resumeRun(request: APIRequestContext, runId: string): Promise<Run> {
  const response = await request.post(`/api/runs/${runId}/resume`);
  if (!response.ok()) {
    throw new Error(`resumeRun failed (${response.status()}): ${await response.text()}`);
  }
  return (await response.json()) as Run;
}

export async function abortRun(request: APIRequestContext, runId: string): Promise<Run> {
  const response = await request.post(`/api/runs/${runId}/abort`);
  if (!response.ok()) {
    throw new Error(`abortRun failed (${response.status()}): ${await response.text()}`);
  }
  return (await response.json()) as Run;
}
