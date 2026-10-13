import type { Page } from '@playwright/test';
import { test, expect } from '../../../test-fixtures';

// A direct load of /runs must hydrate cleanly (#917). A mismatch between the
// server HTML and the client's first render makes React throw away the
// server tree and re-render everything on the client. Nothing else fails when
// that happens, so assert on the console.
function collectHydrationErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error' && /hydrat/i.test(msg.text())) errors.push(msg.text());
  });
  page.on('pageerror', (err) => {
    if (/hydrat/i.test(err.message)) errors.push(err.message);
  });
  return errors;
}

async function loadRuns(page: Page): Promise<void> {
  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
  // Let the runs query settle so a late hydration error still lands here.
  await page.waitForLoadState('networkidle');
}

test('direct load of /runs logs no hydration error', async ({ page }) => {
  const hydrationErrors = collectHydrationErrors(page);
  await loadRuns(page);
  expect(hydrationErrors).toEqual([]);
});

test('direct load of /runs with a persisted active project logs no hydration error', async ({
  page,
}) => {
  // The active project comes from localStorage, which the server can't see,
  // so the project badge must not be part of the first client render either.
  await page.addInitScript(() => {
    window.localStorage.setItem('dap.activeProjectId', 'proj-917-hydration');
  });
  const hydrationErrors = collectHydrationErrors(page);
  await loadRuns(page);
  expect(hydrationErrors).toEqual([]);
});
