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

  // Change-password card renders all three inputs and the submit button.
  // getByLabel is the right idiom for HTML password inputs (which don't
  // have an implicit `textbox` role per the ARIA spec); `exact: true`
  // disambiguates "New password" from "Confirm new password".
  await expect(page.getByLabel('Current password')).toBeVisible();
  await expect(page.getByLabel('New password', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Confirm new password')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Update password' })).toBeVisible();
});
