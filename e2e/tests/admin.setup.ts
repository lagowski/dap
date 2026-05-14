import { test as setup, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { ADMIN_EMAIL, ADMIN_PASSWORD } from '../fixtures';

const AUTH_FILE = resolve(__dirname, '..', '.auth/admin.json');

setup('register admin user, promote via DB, persist session', async ({ page }) => {
  // Step 1: regular signup so a User row + cookie exist. The auto-login
  // path in /api/auth/register lands a session cookie when registration
  // succeeds (no email-verification gate in the default config).
  await page.goto('/signup');
  await expect(page.getByText(/Get started with DAP/)).toBeVisible();
  await page.getByLabel('Email').fill(ADMIN_EMAIL);
  await page.getByLabel('Password', { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByLabel('Confirm password').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/signup'), { timeout: 10_000 });

  // Step 2: promote via direct sqlite3 UPDATE. The engine doesn't expose
  // any bootstrap-admin endpoint, and the smoke-test pattern of writing
  // through an ORM session only works in-process (the Playwright runner
  // is out-of-process from the engine). The DB path is passed in via
  // E2E_DB_PATH, set by playwright.config.ts to the same /tmp file the
  // engine wrapper uses for DAP_DB_PATH.
  const dbPath = process.env.E2E_DB_PATH;
  if (!dbPath) {
    throw new Error('E2E_DB_PATH not set — admin setup misconfigured');
  }
  // Doubled single-quotes is the SQL-standard escape; defends against
  // any future value of ADMIN_EMAIL that contains a literal apostrophe.
  // We can't use real bound parameters here because the sqlite3 CLI's
  // `.param set` only takes effect in interactive mode, not the single-
  // statement invocation below.
  const safeEmail = ADMIN_EMAIL.replaceAll("'", "''");

  // `-cmd 'PRAGMA busy_timeout=...'` runs before the UPDATE so the CLI
  // waits out the aiosqlite writer instead of failing with
  // "database is locked" the moment the engine holds the WAL write lock.
  try {
    execFileSync(
      'sqlite3',
      [
        '-cmd',
        'PRAGMA busy_timeout=10000;',
        dbPath,
        `UPDATE users SET is_superuser = 1 WHERE lower(email) = lower('${safeEmail}');`,
      ],
      { stdio: ['ignore', 'pipe', 'pipe'] },
    );
  } catch (err) {
    const e = err as NodeJS.ErrnoException & { stderr?: Buffer; stdout?: Buffer };
    const stderr = e.stderr?.toString() ?? '';
    const stdout = e.stdout?.toString() ?? '';
    throw new Error(
      `sqlite3 promote failed: ${e.message}\nstderr: ${stderr}\nstdout: ${stdout}`,
    );
  }

  // Step 3: confirm via /api/auth/me that the cookie now represents an
  // admin. SQLAlchemy's connection cache can still serve the pre-promote
  // user row for one request — reload first so any cached state is
  // dropped, then probe the API.
  await page.reload();
  const me = await page.request.get('/api/auth/me');
  expect(me.ok()).toBe(true);
  expect(await me.json()).toMatchObject({
    email: ADMIN_EMAIL,
    is_superuser: true,
  });

  // Step 4: persist storageState for the [admin] project.
  await page.context().storageState({ path: AUTH_FILE });
});
