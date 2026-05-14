import { test, expect } from '@playwright/test';
import { ADMIN_EMAIL, FIXTURE_EMAIL } from '../../../fixtures';

test('admin users — table lists both the fixture user and the admin', async ({ page }) => {
  await page.goto('/admin/users');
  await expect(page.getByRole('heading', { name: 'Users' })).toBeVisible();

  // Setup projects registered both users earlier in the run, so each
  // email is expected to appear in its own table row. We scope by row
  // rather than cell because the admin's own row appends " (you)" to
  // the email and the per-row action buttons embed the email in their
  // accessible names — both confound a direct cell-name match.
  await expect(page.getByRole('row').filter({ hasText: FIXTURE_EMAIL })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: ADMIN_EMAIL })).toBeVisible();
});
