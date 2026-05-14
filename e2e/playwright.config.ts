import { defineConfig } from '@playwright/test';
import { existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';

const REPO_ROOT = resolve(__dirname, '..');

// Guard: if .env.local pins DAP_DB_PATH/DAP_DATABASE_URL the engine wrapper
// below would override the Playwright env var and tests would hit the dev DB.
// Fail loudly at config-load time instead of silently corrupting user data.
const envLocal = resolve(REPO_ROOT, '.env.local');
if (existsSync(envLocal)) {
  const content = readFileSync(envLocal, 'utf8');
  if (/^\s*DAP_DB_PATH\s*=/m.test(content) || /^\s*DAP_DATABASE_URL\s*=/m.test(content)) {
    throw new Error(
      '.env.local sets DAP_DB_PATH or DAP_DATABASE_URL — comment those lines out before running e2e tests.',
    );
  }
}

// Unique per-run DB outside the repo. Avoids rmSync-before-run; a previous
// version did that in globalSetup and the deletion between sync-engine
// bootstrap and async-engine first-connect produced two distinct inodes for
// the same path (async engine read an empty file → "no such table: users").
// Honor any E2E_DB_PATH already in the environment so Playwright workers
// — which re-evaluate the config in fresh Node processes — pick up the
// same value the main process generated. Without the `??` each worker
// would call Date.now() independently and produce a different path,
// leaving admin.setup.ts shelling out to sqlite3 against a non-existent
// DB while the engine wrote to the original.
const TEST_DB = process.env.E2E_DB_PATH ?? join(tmpdir(), `dap-e2e-${Date.now()}.db`);
process.env.E2E_DB_PATH = TEST_DB;

// storageState files produced by the setup projects, consumed by the
// downstream projects. Gitignored — contain session cookies.
const AUTH_FILE = resolve(__dirname, '.auth/user.json');
const ADMIN_AUTH_FILE = resolve(__dirname, '.auth/admin.json');

export default defineConfig({
  fullyParallel: false,
  // `fullyParallel: false` is per-file; `workers: 1` extends serialization
  // across files/projects so all specs share the single webServer + DB
  // without lock contention or state coupling.
  workers: 1,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:3000',
    headless: true,
  },
  projects: [
    // Registers the regular fixture user once per run and persists the
    // session cookie to AUTH_FILE. The `app` project reuses that cookie
    // via storageState.
    {
      name: 'setup',
      testDir: './tests',
      testMatch: /auth\.setup\.ts$/,
    },
    // Registers the admin fixture user and shells out to sqlite3 to flip
    // is_superuser=1 on the test DB. Persists a separate cookie file so
    // admin-scoped specs can't accidentally use the regular user's session.
    {
      name: 'admin-setup',
      testDir: './tests',
      testMatch: /admin\.setup\.ts$/,
    },
    // Auth-flow specs run unauthenticated — they exercise /signup, /login,
    // /forgot-password etc. and must not start from a logged-in storageState.
    {
      name: 'auth',
      testDir: './tests/auth',
    },
    // Everything authenticated as the regular fixture user. The admin
    // subtree is handled by the `admin` project below, so it's excluded
    // here to avoid running the same spec twice.
    {
      name: 'app',
      testDir: './tests/app',
      testIgnore: '**/admin/**',
      dependencies: ['setup'],
      use: { storageState: AUTH_FILE },
    },
    // Admin-scoped specs run as the promoted admin user with their own
    // storageState. Lives under tests/app/admin/ for filesystem locality.
    {
      name: 'admin',
      testDir: './tests/app/admin',
      dependencies: ['admin-setup'],
      use: { storageState: ADMIN_AUTH_FILE },
    },
  ],
  webServer: [
    {
      command: './e2e/run-engine.sh',
      cwd: REPO_ROOT,
      url: 'http://127.0.0.1:7333/health',
      reuseExistingServer: false,
      timeout: 60_000,
      env: { DAP_DB_PATH: TEST_DB },
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: './e2e/run-dashboard.sh',
      cwd: REPO_ROOT,
      url: 'http://127.0.0.1:3000',
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
});
