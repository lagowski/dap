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
const TEST_DB = join(tmpdir(), `dap-e2e-${Date.now()}.db`);

// storageState file produced by the `setup` project, consumed by the `app`
// project. Gitignored — contains a session cookie.
const AUTH_FILE = resolve(__dirname, '.auth/user.json');

export default defineConfig({
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:3000',
    headless: true,
  },
  projects: [
    // Registers a fixture user once per run and persists the session cookie
    // to AUTH_FILE. The `app` project reuses that cookie via storageState.
    {
      name: 'setup',
      testDir: './tests',
      testMatch: /.*\.setup\.ts$/,
    },
    // Auth-flow specs run unauthenticated — they exercise /signup, /login,
    // /forgot-password etc. and must not start from a logged-in storageState.
    {
      name: 'auth',
      testDir: './tests/auth',
    },
    // Everything that needs to be logged in. `dependencies: ['setup']` makes
    // Playwright run the setup project first so AUTH_FILE exists by the time
    // these specs start.
    {
      name: 'app',
      testDir: './tests/app',
      dependencies: ['setup'],
      use: { storageState: AUTH_FILE },
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
