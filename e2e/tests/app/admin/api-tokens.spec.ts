import { test, expect } from '@playwright/test';

// Surface-level smoke only. The list itself is currently broken in the
// dashboard: the data hook calls /api/auth/api-tokens/admin, but the
// catch-all /api/[...path] proxy explicitly excludes /api/auth/*, and no
// dedicated route handler proxies the admin endpoint, so the page
// renders an error banner ("Auth routes are not proxied. Use the
// dashboard's /api/auth/* endpoints.") instead of the table.
//
// File a follow-up to wire up /api/auth/api-tokens/admin and revoke
// flow then extend this spec to cover create-via-API + revoke-via-UI.

test('admin api-tokens — page renders heading and revoked filter control', async ({ page }) => {
  await page.goto('/admin/api-tokens');
  await expect(page.getByRole('heading', { name: 'API tokens' })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Show revoked' })).toBeVisible();
});
