import { test as base } from '@playwright/test';
import { createPipeline, type Pipeline } from './helpers/pipelines';
import { createProject, type Project, type ProjectOverrides } from './helpers/projects';
import { triggerRun, waitForRunCompletion, type Run } from './helpers/runs';
import { createTestUser, type TestUser } from './helpers/users';

type PipelineOverrides = { name?: string; description?: string };
type TrackedKind = 'project' | 'pipeline' | 'agent';
type TestUserOverrides = { email?: string; password?: string };

// Factory + tracking fixtures: seed a resource on demand (or register a
// UI-created resource's id) and archive it in teardown. The per-run /tmp
// DB isolates state between runs, but per-test cleanup satisfies the
// per-issue ACs ("each test creates and cleans up its own data") and
// keeps shared-DB assertions tractable should anyone add empty-state
// checks later.
//
// All DELETE failures during teardown are intentionally swallowed via
// `.catch(() => {})`: an idempotent archive (returns 404 on re-delete,
// 204 the first time) should not turn a passing test into a failed
// teardown. The DB is discarded at end-of-run regardless.
export const test = base.extend<{
  seedPipeline: (overrides?: PipelineOverrides) => Promise<Pipeline>;
  seedProject: (overrides?: ProjectOverrides) => Promise<Project>;
  seedRun: () => Promise<Run>;
  seedAdminTargetUser: (overrides?: TestUserOverrides) => Promise<TestUser>;
  trackResource: (kind: TrackedKind, id: string) => void;
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

  seedRun: async ({ request }, use) => {
    const tracked: Array<{ pipelineId: string; agentId: string }> = [];

    await use(async () => {
      // Trigger a real run against a fresh single-node bash-echo pipeline.
      // The bash adapter extracts `echo ok` from the prompt's <command> tag
      // and completes in ~200ms. We then poll until the run reaches a
      // terminal state so subsequent assertions don't race the executor.
      const pipeline = await createPipeline(request);
      tracked.push({
        pipelineId: pipeline.id,
        agentId: pipeline.nodes[0].agent_id,
      });
      const triggered = await triggerRun(request, pipeline.id);
      return waitForRunCompletion(request, triggered.id);
    });

    // Archive the underlying pipeline + agent. Note: runs are NOT
    // cascade-deleted — RunORM.pipeline_id is a non-cascading FK, so
    // archived pipelines retain their run history, and there's no public
    // DELETE /runs/<id> endpoint to delete the row directly. Seeded run
    // rows therefore persist through the rest of the session. For the
    // per-run /tmp DB this is fine (the DB is discarded between runs),
    // and runs/list.spec relies on alphabetical ordering so the
    // empty-state assertion lands before any run is seeded.
    for (const { pipelineId } of tracked) {
      await request.delete(`/api/pipelines/${pipelineId}`).catch(() => {});
    }
    for (const { agentId } of tracked) {
      await request.delete(`/api/agents/${agentId}`).catch(() => {});
    }
  },

  // Seeds a throwaway user via the engine's POST /auth/register endpoint
  // (called directly on :7333 so the dashboard's auto-login wrapper
  // doesn't replace the admin's cookie on this request context). Used
  // by admin-destructive-action specs that need a target user to
  // promote / suspend / soft-delete. Teardown soft-deletes through the
  // dashboard proxy (admin-only); failures are swallowed because a
  // test that already soft-deleted the user gets 404 on re-delete.
  seedAdminTargetUser: async ({ request }, use) => {
    const tracked: string[] = [];
    await use(async (overrides) => {
      const user = await createTestUser(request, overrides);
      tracked.push(user.id);
      return user;
    });
    for (const id of tracked) {
      await request.delete(`/api/users/${id}`).catch(() => {});
    }
  },

  // For specs that create resources via the UI (not via a seed helper) —
  // pass the new resource's id here so it joins the same teardown cycle.
  trackResource: async ({ request }, use) => {
    const tracked: Array<{ kind: TrackedKind; id: string }> = [];
    await use((kind, id) => {
      tracked.push({ kind, id });
    });
    // Reverse order so dependents (e.g. pipelines that reference an agent)
    // archive before their dependencies.
    for (const { kind, id } of tracked.reverse()) {
      await request.delete(`/api/${kind}s/${id}`).catch(() => {});
    }
  },
});

export { expect } from '@playwright/test';
