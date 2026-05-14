import { test as base } from '@playwright/test';
import { createPipeline, type Pipeline } from './helpers/pipelines';

type PipelineOverrides = { name?: string; description?: string };

// Factory fixture: seeds a pipeline (and its underlying agent) on demand and
// archives both in teardown. The per-run /tmp DB already isolates state
// between runs, but per-test cleanup satisfies the issue ACs ("each test
// creates and cleans up its own data") and keeps shared-DB assertions
// tractable should anyone add empty-state checks later.
export const test = base.extend<{
  seedPipeline: (overrides?: PipelineOverrides) => Promise<Pipeline>;
}>({
  seedPipeline: async ({ request }, use) => {
    const tracked: Array<{ pipelineId: string; agentId: string }> = [];

    await use(async (overrides) => {
      const pipeline = await createPipeline(request, overrides);
      tracked.push({
        pipelineId: pipeline.id,
        agentId: pipeline.nodes[0].agent_id,
      });
      return pipeline;
    });

    // Archive pipelines first so the underlying agent isn't blocked by an
    // active reference; then archive the agents. Failures are swallowed —
    // the per-run DB will be discarded regardless.
    for (const { pipelineId } of tracked) {
      await request.delete(`/api/pipelines/${pipelineId}`).catch(() => {});
    }
    for (const { agentId } of tracked) {
      await request.delete(`/api/agents/${agentId}`).catch(() => {});
    }
  },
});

export { expect } from '@playwright/test';
