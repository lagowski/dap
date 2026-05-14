import { test, expect } from '@playwright/test';

// /admin/settings is a read-only snapshot of instance-level config
// (auth, oauth, cors, storage) — distinct from operator /settings which
// covers runtimes + providers. Verify the four cards render so a
// regression that breaks the GET /settings/admin response surfaces here.

test('admin settings — all four instance-config cards render', async ({ page }) => {
  await page.goto('/admin/settings');
  await expect(page.getByRole('heading', { name: 'Instance settings' })).toBeVisible();

  // shadcn CardTitle renders a <div>, not a heading, so we anchor on
  // exact text match for the four section titles.
  await expect(page.getByText('Authentication', { exact: true })).toBeVisible();
  await expect(page.getByText('OAuth providers', { exact: true })).toBeVisible();
  await expect(page.getByText('CORS', { exact: true })).toBeVisible();
  await expect(page.getByText('Storage', { exact: true })).toBeVisible();
});
