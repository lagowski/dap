import { test, expect } from '@playwright/test';

// /settings is currently an operator/admin **read-only view of engine
// configuration** (runtimes, api-call providers, engine info) — not a
// user profile/password editor. Issue #404's original AC assumed a
// user-settings UI; that UI doesn't exist in the dashboard yet, so the
// view/update-profile/change-password specs called for in the AC are
// inapplicable. This smoke proves the engine-config view renders all
// four sections; once a user-settings UI lands, full CRUD coverage
// belongs in a follow-up.

test('settings — engine config page renders all sections', async ({ page }) => {
  await page.goto('/settings');

  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Runtimes' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'api-call providers' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Engine' })).toBeVisible();
});
