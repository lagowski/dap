import { test, expect } from '@playwright/test';

test('account change-password — old credentials fail login, new ones succeed', async ({
  browser,
}) => {
  // Spin up a fresh browser context so this spec doesn't mutate the
  // fixture user's password — the [app] project shares storageState
  // across files, so changing the fixture user's password would break
  // any spec that later tries to log in as them via the API. The
  // throwaway user dies with the per-run /tmp DB.
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = `e2e-pwchange-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  const oldPassword = 'OldPassword123!';
  const newPassword = 'NewPassword456!';

  try {
    // Register: /api/auth/register auto-logs-in and lands a cookie
    // in this context.
    await page.goto('/signup');
    // /signup page wires Label/Input via shadcn's Form/FormField properly,
    // so getByLabel works here (unlike the /account form's bare Label+Input
    // pair which doesn't expose the label-input association the same way).
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password', { exact: true }).fill(oldPassword);
    await page.getByLabel('Confirm password').fill(oldPassword);
    await page.getByRole('button', { name: 'Create account' }).click();
    await page.waitForURL((url) => !url.pathname.endsWith('/signup'), { timeout: 10_000 });

    // Change password through the new /account page. Password inputs
    // surface as textboxes in Playwright's a11y tree even with type=password.
    await page.goto('/account');
    await page.getByRole('textbox', { name: 'New password', exact: true }).fill(newPassword);
    await page.getByRole('textbox', { name: 'Confirm new password' }).fill(newPassword);
    await page.getByRole('button', { name: 'Update password' }).click();
    await expect(page.getByText('Password updated.')).toBeVisible();

    // Login round-trip: old credentials must fail, new ones must succeed.
    // /api/auth/login proxies to /auth/jwt/login; engine returns 400 on
    // bad credentials and 200 + Set-Cookie on success.
    const badLogin = await page.request.post('/api/auth/login', {
      data: { email, password: oldPassword },
    });
    expect(badLogin.status()).toBeGreaterThanOrEqual(400);

    const goodLogin = await page.request.post('/api/auth/login', {
      data: { email, password: newPassword },
    });
    expect(goodLogin.ok()).toBe(true);
  } finally {
    await context.close();
  }
});
