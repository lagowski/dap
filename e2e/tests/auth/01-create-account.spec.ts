import { test, expect } from '@playwright/test';

test('create new user account', async ({ page }) => {
  await page.goto('/signup');
  // CardTitle "Create account" renders as <div>, not <h1>, so we anchor on
  // the signup-only description text rather than a heading role.
  await expect(page.getByText(/Get started with DAP/)).toBeVisible();

  await page.getByLabel('Email').fill('testuser@example.com');
  await page.getByLabel('Password', { exact: true }).fill('TestPassword123!');
  await page.getByLabel('Confirm password').fill('TestPassword123!');
  await page.getByRole('button', { name: 'Create account' }).click();

  await page.waitForURL((url) => !url.pathname.endsWith('/signup'), { timeout: 10_000 });

  const me = await page.request.get('/api/auth/me');
  expect(me.ok()).toBe(true);
  expect(await me.json()).toMatchObject({ email: 'testuser@example.com' });
});
