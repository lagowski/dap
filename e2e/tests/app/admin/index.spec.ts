import { test, expect } from '@playwright/test';

// Runs under the [admin] project (uses the promoted-admin storageState).
// Sanity-checks that the admin landing page reaches the user and lists
// the four sub-area tiles via their headings/links.

test('admin index — landing page renders all four sub-area links', async ({ page }) => {
  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible();

  // Four navigation links to the sub-areas. Order matches the layout's
  // grid; selector relies on accessible link names so a CSS reshuffle
  // doesn't break the test.
  await expect(page.getByRole('link', { name: /Users/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Audit log/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /API tokens/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Instance settings/ })).toBeVisible();
});
