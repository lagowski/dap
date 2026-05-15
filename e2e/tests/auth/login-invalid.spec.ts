import { test, expect } from '@playwright/test';
import { FIXTURE_EMAIL } from '../../fixtures';

test('login — invalid email format fails client-side', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill('not-an-email');
  await page.getByLabel('Password').fill('anything');
  await page.getByRole('button', { name: 'Sign in' }).click();

  // Zod resolver catches the malformed email before the mutation
  // fires. The inline error message is the Zod-defined string.
  await expect(page.getByText('Enter a valid email')).toBeVisible();
  // Confirm we never navigated.
  await expect(page).toHaveURL(/\/login(\?|$)/);
});

test('login — wrong password renders engine 400 in alert banner', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill(FIXTURE_EMAIL);
  await page.getByLabel('Password').fill('definitely-not-the-real-password');
  await page.getByRole('button', { name: 'Sign in' }).click();

  // The engine returns 400 LOGIN_BAD_CREDENTIALS; dashboard surfaces it
  // via login.isError → role="alert" banner with formatApiError text.
  // We don't pin the exact message wording (engine-side string) —
  // presence of the role=alert banner is the regression signal.
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page).toHaveURL(/\/login(\?|$)/);
});
