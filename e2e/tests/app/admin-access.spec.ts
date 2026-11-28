import { test, expect } from '@playwright/test';

// Runs under the [app] project (regular fixture user, NOT admin). The
// admin layout client-redirects non-admins to /runs on mount, so visiting
// /admin from this storageState should bounce away. Backend enforcement
// (404 anti-enumeration on admin endpoints) is exercised by engine
// pytest, not here.

test('admin access — non-admin user is redirected away from /admin', async ({ page }) => {
  await page.goto('/admin');
  await page.waitForURL(/\/runs\/?$/, { timeout: 10_000 });
  await expect(page.getByRole('heading', { name: 'Administration' })).toHaveCount(0);
});
