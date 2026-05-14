import { test, expect } from '../../../test-fixtures';

test('runs list — seeded run appears in the table', async ({ page, seedRun }) => {
  const run = await seedRun();

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
  // The list link uses the first 8 chars of the run id with a trailing
  // ellipsis ("…"), so we anchor on that prefix substring.
  await expect(
    page.getByRole('link', { name: new RegExp(`^${run.id.slice(0, 8)}`) }),
  ).toBeVisible();
});
