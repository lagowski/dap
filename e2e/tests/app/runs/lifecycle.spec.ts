import { test, expect } from '../../../test-fixtures';
import {
  abortRun,
  pauseRun,
  triggerRun,
  waitForRunStatus,
} from '../../../helpers/runs';
import { createPipeline } from '../../../helpers/pipelines';

// Run lifecycle covers three flows that had zero e2e coverage before:
//   1. Triggering a run via the /runs page's TriggerRunPageDialog
//   2. Aborting a still-running run via the detail page's Abort button
//   3. Resuming a paused run via the detail page's Resume button
//
// The "slow" runs use a single-node `sleep N` bash pipeline so the run
// stays in `running` state long enough for the UI to surface the action
// buttons. Default timeout_ms on agents is 60s; we use sleepSeconds=15
// which leaves ample headroom for the test to interact.

test('runs trigger — TriggerRunPageDialog launches a run + redirects to detail', async ({
  page,
  seedPipeline,
}) => {
  const pipeline = await seedPipeline();

  await page.goto('/runs');
  // First "Trigger run" button is the page-header CTA; the empty-state
  // duplicate doesn't appear when seedPipeline already created a run
  // via seedRun in earlier specs, but `.first()` makes the spec robust
  // either way.
  await page.getByRole('button', { name: 'Trigger run' }).first().click();

  // Dialog opens with a native <select> by id "trigger-pipeline".
  await expect(page.getByRole('heading', { name: 'Trigger run' })).toBeVisible();
  await page.selectOption('select#trigger-pipeline', pipeline.id);
  await page.getByRole('button', { name: /^Run$/ }).click();

  // useTriggerRun mutation resolves → router.push to /runs/<new id>.
  await page.waitForURL(/\/runs\/[^/]+$/, { timeout: 15_000 });

  // Confirm the detail page actually rendered after the navigation —
  // a regression that redirects to the URL but fails to render the run
  // shell would otherwise pass. The h1 displays the full run id; we
  // extract it from the URL and assert it shows up as the heading.
  const newRunId = page.url().match(/\/runs\/([^/]+)$/)?.[1];
  expect(newRunId).toBeTruthy();
  await expect(page.getByRole('heading', { name: newRunId! })).toBeVisible();
});

test('runs abort — clicking Abort on a running run drives final_status to aborted', async ({
  page,
  request,
}) => {
  // Slow pipeline so the run is still "running" when we click Abort.
  const pipeline = await createPipeline(request, { sleepSeconds: 15 });
  const triggered = await triggerRun(request, pipeline.id);

  // Confirm we caught it mid-flight before navigating — otherwise the
  // RunActions component renders nothing (status === success).
  await waitForRunStatus(request, triggered.id, (s) => s === 'running', 5_000);

  await page.goto(`/runs/${triggered.id}`);
  page.once('dialog', (d) => {
    d.accept().catch(() => {
      // dialog races with the post-click navigation/re-render
    });
  });
  await page.getByRole('button', { name: 'Abort' }).click();

  // Poll until the engine settles the abort. The bash subprocess
  // continues sleeping (langgraph can't interrupt a syscall mid-flight),
  // but final_status flips to "aborted" once the supersteps run their
  // pause/abort handler.
  const final = await waitForRunStatus(
    request,
    triggered.id,
    (s) => s === 'aborted',
    20_000,
  );
  expect(final.final_status).toBe('aborted');

  // Reload so the UI's useRun query refetches and renders the terminal
  // state — proves the dashboard surfaces the abort, not just the API.
  await page.reload();
  await expect(page.getByText('aborted', { exact: true })).toBeVisible();
});

test('runs resume — clicking Resume on a paused run flips it back to running/finishing', async ({
  page,
  request,
}) => {
  // Pre-pause via API so the test starts in the paused state. Clicking
  // Pause via UI mid-flight is racy on single-node pipelines (the bash
  // subprocess completes before langgraph checks the pause flag), so
  // we exercise the Resume button — the harder, less-tested half — and
  // leave the Pause button's contract to the engine's pytest layer.
  const pipeline = await createPipeline(request, { sleepSeconds: 10 });
  const triggered = await triggerRun(request, pipeline.id);
  await waitForRunStatus(request, triggered.id, (s) => s === 'running', 5_000);
  await pauseRun(request, triggered.id);
  await waitForRunStatus(request, triggered.id, (s) => s === 'paused', 15_000);

  await page.goto(`/runs/${triggered.id}`);
  await page.getByRole('button', { name: 'Resume' }).click();

  // After resume the run continues running (and eventually finishes).
  const final = await waitForRunStatus(
    request,
    triggered.id,
    (s) => s !== 'paused',
    20_000,
  );
  expect(['running', 'success']).toContain(final.final_status);

  // UI side: after refetch the Resume button (only rendered for
  // status === "paused") must be gone, proving the dashboard moved
  // out of the paused branch — not just that the API state changed.
  await page.reload();
  await expect(page.getByRole('button', { name: 'Resume' })).toHaveCount(0);

  // Cleanup: if it's still running, abort so we don't wait the full
  // sleep N seconds on teardown.
  if (final.final_status === 'running') {
    await abortRun(request, triggered.id).catch(() => {});
  }
});
