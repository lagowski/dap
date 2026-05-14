import { test, expect } from '../../../test-fixtures';

test('runs view — detail page renders id, status, metrics, and graph', async ({
  page,
  seedRun,
}) => {
  const run = await seedRun();

  await page.goto(`/runs/${run.id}`);

  // Heading renders the full run id (monospace).
  await expect(page.getByRole('heading', { name: run.id })).toBeVisible();

  // RunStatusBadge renders the run's final_status literally (e.g. "success").
  // Asserting on the actual value caught from the seed run keeps the test
  // honest about what state the dashboard is meant to surface.
  await expect(page.getByText(run.final_status, { exact: true })).toBeVisible();

  // Metric grid labels — present regardless of state. `exact: true`
  // disambiguates from the sidebar's "Pipelines" link and the async
  // "Loading pipeline…" status, both of which contain "Pipeline" as a
  // substring.
  await expect(page.getByText('Pipeline', { exact: true })).toBeVisible();
  await expect(page.getByText('Started', { exact: true })).toBeVisible();

  // PipelineGraph is async-loaded; the dashboard shows a "Loading
  // pipeline…" Card while the GET /pipelines/<id> request resolves. Wait
  // for that to leave so we know the graph contract (PipelineGraph rendered
  // with the run's nodes) was actually exercised, not just stubbed.
  await expect(page.getByText('Loading pipeline…')).toBeHidden({ timeout: 10_000 });
});
