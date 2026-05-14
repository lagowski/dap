import { test, expect } from '../../../test-fixtures';

test('projects delete (archive) — gone from list after confirming dialog', async ({
  page,
  seedProject,
}) => {
  const project = await seedProject();

  await page.goto('/projects');
  const row = page.getByRole('row').filter({ hasText: project.name });
  await expect(row).toBeVisible();

  // Archive button triggers window.confirm() — accept before clicking.
  page.once('dialog', (dialog) => {
    dialog.accept().catch(() => {
      // dialog can race with the subsequent re-render; ignore late-accept errors
    });
  });
  await row.getByRole('button', { name: 'Archive' }).click();

  // Archived projects drop out of the default list query.
  await expect(page.getByRole('link', { name: project.name })).toHaveCount(0);
});
