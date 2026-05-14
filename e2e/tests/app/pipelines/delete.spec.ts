import { test, expect } from '@playwright/test';
import { createPipeline } from '../../../helpers/pipelines';

test('pipelines delete (archive) — gone from list after confirming dialog', async ({
  page,
  request,
}) => {
  const pipeline = await createPipeline(request);

  await page.goto('/pipelines');
  const row = page.getByRole('row').filter({ hasText: pipeline.name });
  await expect(row).toBeVisible();

  // Archive button triggers window.confirm() — accept before clicking.
  page.once('dialog', (dialog) => {
    dialog.accept().catch(() => {
      // dialog can race with the subsequent re-render; ignore late-accept errors
    });
  });
  await row.getByRole('button', { name: 'Archive' }).click();

  // Archived pipelines drop out of the default list query.
  await expect(page.getByRole('cell', { name: pipeline.name })).toHaveCount(0);
});
