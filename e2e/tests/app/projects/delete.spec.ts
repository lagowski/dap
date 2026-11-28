import { test, expect } from '../../../test-fixtures';
import { confirmDestructive } from '../../../helpers/confirm';

test('projects delete (archive) — gone from list after confirming dialog', async ({
  page,
  seedProject,
}) => {
  const project = await seedProject();

  await page.goto('/projects');
  const row = page.getByRole('row').filter({ hasText: project.name });
  await expect(row).toBeVisible();

  // Archive opens the in-page ConfirmDestructiveDialog. Confirming it (and
  // waiting for it to close) is what makes the toHaveCount(0) below
  // meaningful — while the dialog is open the page is aria-hidden and the
  // assertion would pass without anything having been archived.
  await row.getByRole('button', { name: 'Archive' }).click();
  await confirmDestructive(page, 'Archive');

  // Archived projects drop out of the default list query.
  await expect(page.getByRole('link', { name: project.name })).toHaveCount(0);
});
