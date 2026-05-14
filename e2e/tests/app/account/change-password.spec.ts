import { test, expect } from '@playwright/test';

// Run in a fresh, unauthenticated context so this spec doesn't mutate
// the [app] project's shared fixture user. `test.use({ storageState })`
// here overrides only this file — page + request fixtures still pick
// up `baseURL` and the rest of the project's `use` block.
test.use({ storageState: { cookies: [], origins: [] } });

test('account change-password — current-password gate + login round-trip', async ({
  page,
  request,
}) => {
  const email = `e2e-pwchange-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  const oldPassword = 'OldPassword123!';
  const newPassword = 'NewPassword456!';

  // Register: /api/auth/register auto-logs-in and lands a cookie.
  await page.goto('/signup');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(oldPassword);
  await page.getByLabel('Confirm password').fill(oldPassword);
  await page.getByRole('button', { name: 'Create account' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/signup'), { timeout: 10_000 });

  await page.goto('/account');

  // Wrong current password is rejected client-side without touching
  // PATCH /users/me — the form drives a login-verify step first.
  await page.getByLabel('Current password').fill('not-the-current-password');
  await page.getByLabel('New password', { exact: true }).fill(newPassword);
  await page.getByLabel('Confirm new password').fill(newPassword);
  await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByText('Current password is incorrect')).toBeVisible();

  // Now do it for real.
  await page.getByLabel('Current password').fill(oldPassword);
  await page.getByLabel('New password', { exact: true }).fill(newPassword);
  await page.getByLabel('Confirm new password').fill(newPassword);
  await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByText('Password updated.')).toBeVisible();

  // Login round-trip: old credentials must fail with the engine's
  // bad-credentials response (400 from fastapi-users; 500 here would
  // mean the engine regressed and we want that to fail the test).
  const badLogin = await request.post('/api/auth/login', {
    data: { email, password: oldPassword },
  });
  expect(badLogin.status()).toBe(400);

  // New credentials must succeed.
  const goodLogin = await request.post('/api/auth/login', {
    data: { email, password: newPassword },
  });
  expect(goodLogin.ok()).toBe(true);
});
