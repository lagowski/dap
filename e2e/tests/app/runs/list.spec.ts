import { test, expect } from '../../../test-fixtures';

test('runs list — empty state, then seeded run appears after reload', async ({
  page,
  seedRun,
}) => {
  // Empty state runs first. Within the [app] project no test creates a run
  // before this one (alphabetical order puts agents/, pipelines/, projects/
  // before runs/, and none of those specs trigger runs), so the /runs query
  // returns 0 items here.
  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
  await expect(page.getByText('No runs yet', { exact: false })).toBeVisible();

  // Now seed a run and verify the table picks it up.
  const run = await seedRun();
  await page.reload();
  // The list link uses the first 8 chars of the run id with a trailing
  // ellipsis ("…"), so we anchor on that prefix.
  await expect(
    page.getByRole('link', { name: new RegExp(`^${run.id.slice(0, 8)}`) }),
  ).toBeVisible();
});
