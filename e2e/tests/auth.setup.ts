import { test as setup, expect } from '@playwright/test';
import { resolve } from 'node:path';
import { FIXTURE_EMAIL, FIXTURE_PASSWORD } from '../fixtures';

const AUTH_FILE = resolve(__dirname, '..', '.auth/user.json');

setup('register fixture user and persist session', async ({ page }) => {
  await page.goto('/signup');
  await expect(page.getByText(/Get started with DAP/)).toBeVisible();

  await page.getByLabel('Email').fill(FIXTURE_EMAIL);
  await page.getByLabel('Password', { exact: true }).fill(FIXTURE_PASSWORD);
  await page.getByLabel('Confirm password').fill(FIXTURE_PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).click();

  await page.waitForURL((url) => !url.pathname.endsWith('/signup'), { timeout: 10_000 });

  const me = await page.request.get('/api/auth/me');
  expect(me.ok()).toBe(true);
  expect(await me.json()).toMatchObject({ email: FIXTURE_EMAIL });

  await page.context().storageState({ path: AUTH_FILE });
});
