import { test, expect } from '@playwright/test';
import { FIXTURE_EMAIL } from '../../../fixtures';

test('account view — profile card shows fixture email + change-password form renders', async ({
  page,
}) => {
  await page.goto('/account');
  await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible();

  // Profile card shows the current user's email (read-only). Scope to
  // <main> so we don't also match the user-menu's email pill in the
  // sidebar.
  await expect(page.getByRole('main').getByText(FIXTURE_EMAIL)).toBeVisible();

  // Change-password card renders both inputs and the submit button.
  // Anchor on textbox role + accessible name: the password inputs
  // surface as textboxes in Playwright's tree even with type="password".
  await expect(page.getByRole('textbox', { name: 'New password', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Confirm new password' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Update password' })).toBeVisible();
});
