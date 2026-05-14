import { test, expect } from '@playwright/test';

// The fixture user and the admin user both went through /auth/register
// during their respective setup projects, so by the time this spec
// runs there are at least two `user.registered` rows in the audit log.
// Filtering for that event type and asserting on the presence of rows
// proves the page reads the audit table and renders entries.

test('admin audit-log — filter by event type surfaces registration rows', async ({ page }) => {
  await page.goto('/admin/audit-log');
  await expect(page.getByRole('heading', { name: 'Audit log' })).toBeVisible();

  // Both filter inputs render with their documented placeholders.
  await expect(page.getByPlaceholder('user.logged_in')).toBeVisible();
  await expect(page.getByPlaceholder('UUID')).toBeVisible();

  // Filter for user.registered so the assertion isn't sensitive to which
  // event types happen to be paginated into view at run time.
  await page.getByPlaceholder('user.logged_in').fill('user.registered');

  // The event_type cell renders the literal code in a <code> element.
  // Two registrations earlier in this run guarantee ≥1 row visible.
  await expect(page.getByText('user.registered').first()).toBeVisible();
});
