import { test, expect } from '../../../test-fixtures';
import { confirmDestructive } from '../../../helpers/confirm';

test('pipelines delete (archive) — gone from list after confirming dialog', async ({
  page,
  seedPipeline,
}) => {
  const pipeline = await seedPipeline();

  await page.goto('/pipelines');
  const row = page.getByRole('row').filter({ hasText: pipeline.name });
  await expect(row).toBeVisible();

  // Archive opens the in-page ConfirmDestructiveDialog. Confirming it (and
  // waiting for it to close) is what makes the toHaveCount(0) below
  // meaningful — while the dialog is open the page is aria-hidden and the
  // assertion would pass without anything having been archived.
  await row.getByRole('button', { name: 'Archive' }).click();
  await confirmDestructive(page, 'Archive');

  // Archived pipelines drop out of the default list query.
  await expect(page.getByRole('cell', { name: pipeline.name })).toHaveCount(0);
});
