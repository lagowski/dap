import { test, expect } from '@playwright/test';
import { FIXTURE_EMAIL } from '../../fixtures';

test('app smoke — storageState carries the fixture session', async ({ page }) => {
  // If storageState weren't loaded, middleware would bounce us to /login.
  await page.goto('/');
  await expect(page).not.toHaveURL(/\/login(\?|$)/);

  const me = await page.request.get('/api/auth/me');
  expect(me.ok()).toBe(true);
  expect(await me.json()).toMatchObject({ email: FIXTURE_EMAIL });
});
