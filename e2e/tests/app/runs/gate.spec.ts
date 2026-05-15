import { test, expect } from '../../../test-fixtures';
import { triggerRun, waitForRunStatus } from '../../../helpers/runs';
import { createPipeline } from '../../../helpers/pipelines';

// Human-in-loop gate flow: pipelines whose
// `defaults.approval_required_nodes` lists a node id drive the
// langgraph runner's `interrupt_before` — the run pauses BEFORE
// executing that node, hitting final_status="paused" with
// paused_at_node set. The dashboard's GatePanel surfaces an Approve +
// Abort pair on the run detail page. Zero coverage before; this spec
// pins the contract.

async function seedAndAwaitPaused(
  request: import('@playwright/test').APIRequestContext,
) {
  const pipeline = await createPipeline(request, { gateNodes: ['n1'] });
  const triggered = await triggerRun(request, pipeline.id);
  // Engine pauses before n1 because of approval_required_nodes — typical
  // wall time ~200-500ms on a warm engine.
  await waitForRunStatus(request, triggered.id, (s) => s === 'paused', 10_000);
  return triggered;
}

test('gate approve — clicking Approve resumes the paused run to completion', async ({
  page,
  request,
}) => {
  const run = await seedAndAwaitPaused(request);

  await page.goto(`/runs/${run.id}`);
  await expect(page.getByText('Paused — waiting for approval')).toBeVisible();

  await page.getByRole('button', { name: 'Approve' }).click();

  // After approval the runner resumes n1 (bash echo ok), which finishes
  // fast and drives final_status="success".
  const final = await waitForRunStatus(
    request,
    run.id,
    (s) => s === 'success' || s === 'failed',
    15_000,
  );
  expect(final.final_status).toBe('success');
});

test('gate abort — clicking Abort on a paused-at-gate run drives final_status to aborted', async ({
  page,
  request,
}) => {
  const run = await seedAndAwaitPaused(request);

  await page.goto(`/runs/${run.id}`);
  await expect(page.getByText('Paused — waiting for approval')).toBeVisible();

  // Two Abort buttons exist when paused-at-gate: one from RunActions
  // (header) and one from GatePanel (amber card). Both call the same
  // mutation against the same run id, so .first() is fine.
  page.once('dialog', (d) => {
    d.accept().catch(() => {
      // dialog races with the subsequent state refresh
    });
  });
  await page.getByRole('button', { name: 'Abort' }).first().click();

  const final = await waitForRunStatus(
    request,
    run.id,
    (s) => s === 'aborted',
    15_000,
  );
  expect(final.final_status).toBe('aborted');
});
