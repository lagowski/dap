import { test, expect } from '../../../test-fixtures';

test('runs view — detail page renders for seeded run', async ({ page, seedRun }) => {
  const run = await seedRun();

  await page.goto(`/runs/${run.id}`);
  // Detail heading renders the full run id (monospace).
  await expect(page.getByRole('heading', { name: run.id })).toBeVisible();

  // Metric panel labels — present regardless of final_status. We don't
  // assert on the values themselves (timestamps/IDs are dynamic) because
  // their presence is the contract the dashboard guarantees. `exact: true`
  // disambiguates from the sidebar's "Pipelines" link and the async
  // "Loading pipeline…" status, both of which contain the substring.
  await expect(page.getByText('Pipeline', { exact: true })).toBeVisible();
  await expect(page.getByText('Started', { exact: true })).toBeVisible();
});
