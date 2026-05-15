import { test, expect } from '@playwright/test';

// Detail pages for runs / projects / agents all render an "error" Card
// when the data fetch returns 404 (engine response for a nonexistent
// id under each resource). Without this regression test a future
// refactor could render a blank page or unhandled error overlay and
// CI wouldn't catch it. The all-zeros UUID is syntactically valid (so
// the engine reaches its "not found" branch, not a 422) and is
// guaranteed not to exist in the per-run /tmp DB.

const ZERO_UUID = '00000000-0000-0000-0000-000000000000';

test('runs detail — unknown id renders error card, not blank', async ({ page }) => {
  await page.goto(`/runs/${ZERO_UUID}`);

  // URL must stay — no redirect to /login (auth ok) and no client-side
  // bounce somewhere else.
  await expect(page).toHaveURL(new RegExp(`/runs/${ZERO_UUID}$`));

  // Successful detail would show the metric grid labels ("Pipeline",
  // "Started", "Trigger") — none of those should be present.
  await expect(page.getByText('Started', { exact: true })).toHaveCount(0);

  // Error card uses Tailwind's destructive accent — proves the page
  // rendered the failure state instead of a blank fallback.
  await expect(page.locator('.text-destructive').first()).toBeVisible();
});

test('projects detail — unknown id renders error card, not blank', async ({ page }) => {
  await page.goto(`/projects/${ZERO_UUID}`);
  await expect(page).toHaveURL(new RegExp(`/projects/${ZERO_UUID}$`));
  // Project detail success state shows the project name as <h1>;
  // counting that to zero is the negative signal.
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(0);
  await expect(page.locator('.text-destructive').first()).toBeVisible();
});

test('agents detail — unknown id renders error card, not blank', async ({ page }) => {
  await page.goto(`/agents/${ZERO_UUID}`);
  await expect(page).toHaveURL(new RegExp(`/agents/${ZERO_UUID}$`));
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(0);
  await expect(page.locator('.text-destructive').first()).toBeVisible();
});
