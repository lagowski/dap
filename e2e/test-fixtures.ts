import { test as base } from '@playwright/test';
import { createPipeline, type Pipeline } from './helpers/pipelines';
import { createProject, type Project, type ProjectOverrides } from './helpers/projects';

type PipelineOverrides = { name?: string; description?: string };

// Factory fixtures: seed a resource on demand and archive it (and any
// implicitly-created dependencies) on teardown. The per-run /tmp DB
// isolates state between runs, but per-test cleanup satisfies the per-issue
// ACs ("each test creates and cleans up its own data") and keeps
// shared-DB assertions tractable should anyone add empty-state checks
// later.
export const test = base.extend<{
  seedPipeline: (overrides?: PipelineOverrides) => Promise<Pipeline>;
  seedProject: (overrides?: ProjectOverrides) => Promise<Project>;
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
    // active reference; then archive the agents.
    for (const { pipelineId } of tracked) {
      await request.delete(`/api/pipelines/${pipelineId}`).catch(() => {});
    }
    for (const { agentId } of tracked) {
      await request.delete(`/api/agents/${agentId}`).catch(() => {});
    }
  },

  seedProject: async ({ request }, use) => {
    const tracked: string[] = [];

    await use(async (overrides) => {
      const project = await createProject(request, overrides);
      tracked.push(project.id);
      return project;
    });

    for (const id of tracked) {
      await request.delete(`/api/projects/${id}`).catch(() => {});
    }
  },
});

export { expect } from '@playwright/test';
