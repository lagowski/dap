import { readFileSync } from 'node:fs';
import path from 'node:path';

import type { Page } from '@playwright/test';

import { test, expect } from '../../../test-fixtures';
import { createAgent } from '../../../helpers/agents';
import { uniquePipelineName } from '../../../helpers/pipelines';

// #1033 round 3: an imported or long pipeline opens readable. Each bundle is
// a shape that used to open as a stack or as one long row; the designer must
// re-lay it out (snake rows of 6-8) so that, in a 1600x900 window, the graph
// fits at zoom >= 0.4 with no node outside the pane and no two nodes
// overlapping, both on the first open and after the autosaved layout is
// reopened.

interface Bundle {
  name: string;
  description: string;
  entry_point: string;
  nodes: Array<{ id: string; position: { x: number; y: number } }>;
  edges: Array<{ id: string; source: string; target: string; condition?: unknown }>;
  ui_metadata?: Record<string, unknown>;
}

const FIXTURES = path.join(__dirname, 'fixtures', 'layout');
const BUNDLES = ['chain-26', 'chain-fanout-5x5', 'chain-4', 'long-row-22'];
const READABLE_ZOOM = 0.4;

function loadBundle(name: string): Bundle {
  return JSON.parse(readFileSync(path.join(FIXTURES, `${name}.json`), 'utf8')) as Bundle;
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

async function expectReadable(page: Page, nodeCount: number): Promise<void> {
  const nodes = page.locator('.react-flow__node');
  await expect(nodes).toHaveCount(nodeCount);
  // fitView settles after the nodes are measured; poll until the frame holds.
  await expect(async () => {
    const transform = await page
      .locator('.react-flow__viewport')
      .evaluate((el) => (el as HTMLElement).style.transform);
    const zoom = Number(/scale\(([\d.]+)\)/.exec(transform)?.[1] ?? NaN);
    expect(zoom, `viewport transform ${transform}`).toBeGreaterThanOrEqual(READABLE_ZOOM);

    const pane = await page.locator('.react-flow').first().boundingBox();
    expect(pane).not.toBeNull();
    const boxes: Box[] = [];
    for (const handle of await nodes.all()) {
      const box = await handle.boundingBox();
      expect(box).not.toBeNull();
      boxes.push(box!);
    }
    const outside = boxes.filter(
      (b) =>
        b.x < pane!.x - 1 ||
        b.y < pane!.y - 1 ||
        b.x + b.width > pane!.x + pane!.width + 1 ||
        b.y + b.height > pane!.y + pane!.height + 1,
    );
    expect(outside, 'nodes outside the pane').toEqual([]);

    let overlaps = 0;
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i];
        const b = boxes[j];
        if (
          a.x + 1 < b.x + b.width &&
          b.x + 1 < a.x + a.width &&
          a.y + 1 < b.y + b.height &&
          b.y + 1 < a.y + a.height
        ) {
          overlaps += 1;
        }
      }
    }
    expect(overlaps, 'overlapping node pairs').toBe(0);
  }).toPass({ timeout: 15_000 });
}

test.describe('designer opens long and imported pipelines readable (#1033)', () => {
  test.use({ viewport: { width: 1600, height: 900 } });

  const created: { pipelines: string[]; agents: string[] } = { pipelines: [], agents: [] };

  test.afterEach(async ({ request }) => {
    for (const id of created.pipelines.splice(0)) {
      await request.delete(`/api/pipelines/${id}`).catch(() => {});
    }
    for (const id of created.agents.splice(0)) {
      await request.delete(`/api/agents/${id}`).catch(() => {});
    }
  });

  for (const name of BUNDLES) {
    test(`${name}: zoom >= ${READABLE_ZOOM}, 0 outside the pane, 0 overlaps, on open and reopen`, async ({
      page,
      request,
    }) => {
      const bundle = loadBundle(name);
      const agent = await createAgent(request);
      created.agents.push(agent.id);
      const response = await request.post('/api/pipelines', {
        data: {
          name: uniquePipelineName(`e2e-layout-${name}`),
          description: bundle.description,
          schema_version: 'langgraph/1.0',
          state_schema_ref: 'PipelineState.v1',
          entry_point: bundle.entry_point,
          nodes: bundle.nodes.map((n) => ({ ...n, agent_id: agent.id })),
          edges: bundle.edges,
          defaults: { requires_terminal_final_status: false },
          ...(bundle.ui_metadata ? { ui_metadata: bundle.ui_metadata } : {}),
        },
      });
      expect(response.ok(), await response.text()).toBe(true);
      const pipeline = (await response.json()) as { id: string };
      created.pipelines.push(pipeline.id);

      // Every bundle is re-laid out on open, and the layout autosave persists it.
      const saved = page.waitForResponse(
        (r) =>
          r.request().method() === 'PATCH' &&
          r.url().endsWith(`/api/pipelines/${pipeline.id}/ui-metadata`),
      );
      await page.goto(`/pipelines/${pipeline.id}/edit`);
      await expectReadable(page, bundle.nodes.length);
      expect((await saved).ok()).toBe(true);

      await page.reload();
      await expectReadable(page, bundle.nodes.length);
    });
  }
});
