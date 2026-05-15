import { test, expect } from '@playwright/test';
import { FIXTURE_EMAIL, FIXTURE_PASSWORD } from '../../fixtures';

// Login happy path under the [auth] project (no storageState, so the
// page loads truly anonymous). The fixture user was registered by the
// [setup] project earlier in the run, so the credentials are guaranteed
// to exist by the time this spec executes.

test('login — valid credentials redirect to /', async ({ page }) => {
  await page.goto('/login');
  // CardTitle "Sign in" renders as a <div>, not a heading — anchor on
  // the unique card description text instead.
  await expect(page.getByText('Welcome back to DAP.')).toBeVisible();

  await page.getByLabel('Email').fill(FIXTURE_EMAIL);
  await page.getByLabel('Password').fill(FIXTURE_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();

  // Default redirect after login is "/" (sanitised "next" param).
  await page.waitForURL((url) => url.pathname !== '/login', { timeout: 10_000 });
  // Sanity-check the cookie actually landed.
  const me = await page.request.get('/api/auth/me');
  expect(me.ok()).toBe(true);
  expect(await me.json()).toMatchObject({ email: FIXTURE_EMAIL });
});

test('login — honors ?next= redirect target', async ({ page }) => {
  await page.goto('/login?next=/agents');
  await page.getByLabel('Email').fill(FIXTURE_EMAIL);
  await page.getByLabel('Password').fill(FIXTURE_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();

  // sanitiseNext() allows same-origin paths, so /agents survives the
  // safety check and the router.replace lands there directly.
  await page.waitForURL(/\/agents\/?$/, { timeout: 10_000 });
});
