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
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const response = await request.get(`/api/runs/${runId}`);
    if (response.ok()) {
      const run = (await response.json()) as Run;
      if (run.final_status !== 'running') return run;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Run ${runId} did not complete within ${timeoutMs}ms`);
}
