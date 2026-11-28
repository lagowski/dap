import type { APIRequestContext } from '@playwright/test';

// The engine listens directly on :7333. Hitting it via the dashboard's
// /api/auth/register proxy would auto-login the new user and replace
// the current admin's cookie on the request context — exactly what
// we want to avoid. Going to the engine directly returns just the
// User row.
const ENGINE_URL = 'http://127.0.0.1:7333';

export interface TestUser {
  id: string;
  email: string;
  password: string;
}

export function uniqueUserEmail(prefix = 'e2e-target'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@example.com`;
}

export async function createTestUser(
  request: APIRequestContext,
  overrides: { email?: string; password?: string } = {},
): Promise<TestUser> {
  const email = overrides.email ?? uniqueUserEmail();
  const password = overrides.password ?? 'TargetPassword123!';
  const response = await request.post(`${ENGINE_URL}/auth/register`, {
    data: { email, password },
  });
  if (!response.ok()) {
    throw new Error(
      `Failed to seed test user (status ${response.status()}): ${await response.text()}`,
    );
  }
  const body = (await response.json()) as { id: string; email: string };
  return { id: body.id, email: body.email, password };
}
