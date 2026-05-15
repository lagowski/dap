import { test, expect } from '@playwright/test';

test("signup — password and confirm don't match -> inline error", async ({ page }) => {
  await page.goto('/signup');
  await page.getByLabel('Email').fill(`e2e-mismatch-${Date.now()}@example.com`);
  await page.getByLabel('Password', { exact: true }).fill('OnePassword123!');
  // Different value on the confirm field — Zod refine fires on submit.
  await page.getByLabel('Confirm password').fill('AnotherPassword456!');
  await page.getByRole('button', { name: 'Create account' }).click();

  await expect(page.getByText("Passwords don't match")).toBeVisible();
  await expect(page).toHaveURL(/\/signup(\?|$)/);
});

test('signup — short password fails the min-length rule', async ({ page }) => {
  await page.goto('/signup');
  await page.getByLabel('Email').fill(`e2e-short-${Date.now()}@example.com`);
  // 7 chars — engine UserManager + Zod both enforce ≥ 8.
  await page.getByLabel('Password', { exact: true }).fill('short77');
  await page.getByLabel('Confirm password').fill('short77');
  await page.getByRole('button', { name: 'Create account' }).click();

  await expect(page.getByText('Password must be at least 8 characters')).toBeVisible();
});
