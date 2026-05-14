import { test, expect } from '@playwright/test';

// Full CRUD now that the dashboard's catch-all proxy lets /auth/api-tokens
// through (previous version was blocked, leaving an error banner on the
// page — see #415). Spec runs under [admin] so the user-scoped POST seeds
// a token owned by the admin, which the same admin can then see in the
// list and revoke through the UI.

test('admin api-tokens — create via API, list, revoke via UI', async ({ page, request }) => {
  const tokenName = `e2e-token-${Date.now()}`;

  // Seed a token via the user-scoped POST. Response contains the
  // one-time secret value but the test only cares about the row
  // appearing in the admin list.
  const createResp = await request.post('/api/auth/api-tokens', {
    data: { name: tokenName },
  });
  expect(createResp.ok()).toBe(true);

  await page.goto('/admin/api-tokens');
  await expect(page.getByRole('heading', { name: 'API tokens' })).toBeVisible();

  const row = page.getByRole('row').filter({ hasText: tokenName });
  await expect(row).toBeVisible();
  await expect(row.getByText('active', { exact: true })).toBeVisible();

  // Revoke button uses an aria-label like "Revoke <name> (<email>)".
  // window.confirm() fires on click — accept before clicking so the
  // mutation actually fires.
  page.once('dialog', (dialog) => {
    dialog.accept().catch(() => {
      // dialog can race with the subsequent re-render
    });
  });
  await row.getByRole('button', { name: new RegExp(`^Revoke ${tokenName}`) }).click();

  // The row stays visible (default "Show revoked" is on) and the status
  // badge flips from "active" to "revoked".
  await expect(row.getByText('revoked', { exact: true })).toBeVisible();
});
