import { test, expect } from '@playwright/test';

// Detail pages for runs / projects / agents all render an "error" Card
// when the data fetch returns 404 (engine response for a nonexistent
// id under each resource). Without this regression test a future
// refactor could render a blank page or unhandled error overlay and
// CI wouldn't catch it. The all-zeros UUID is syntactically valid (so
// the engine reaches its "not found" branch, not a 422) and is
// guaranteed not to exist in the per-run /tmp DB.
//
// Each test asserts THREE things to pin the full contract:
//   1. URL stays (no redirect to /login or /404 fallback)
//   2. App chrome is still rendered (sidebar nav <aside role=
//      "complementary"> visible) — proves it's an inline error card,
//      not a full-page replacement
//   3. The error message contains "not found" — the engine raises
//      NotFoundError("Agent not found: <id>") / "Pipeline not found: …"
//      / "Run not found: …" via persistence layer, all of which the
//      catch-all wraps into the page's error <Card>.

const ZERO_UUID = '00000000-0000-0000-0000-000000000000';

async function assertNotFoundCard(
  page: import('@playwright/test').Page,
  path: string,
) {
  await page.goto(path);
  await expect(page).toHaveURL(new RegExp(`${path}$`));
  // Sidebar still rendered → chrome intact, this is an inline error.
  await expect(page.getByRole('complementary')).toBeVisible();
  // Error message text — accept either "not found" (agents / projects
  // render formatApiError(error), which surfaces the engine's
  // NotFoundError("X not found: <id>") detail) OR "error 404" (runs
  // render the raw `(error as Error).message`, which ApiError formats
  // as "API error 404"). The inconsistency between detail pages is
  // documented elsewhere as a follow-up; the contract this spec pins
  // is just "some failure indicator is shown".
  await expect(
    page.getByRole('main').getByText(/(not found|error\s*404)/i),
  ).toBeVisible();
}

test('runs detail — unknown id renders error card with chrome intact', async ({ page }) => {
  await assertNotFoundCard(page, `/runs/${ZERO_UUID}`);
  // Successful detail would show the metric grid labels — ensure none
  // of them slipped through alongside the error state.
  await expect(page.getByText('Started', { exact: true })).toHaveCount(0);
});

test('projects detail — unknown id renders error card with chrome intact', async ({ page }) => {
  await assertNotFoundCard(page, `/projects/${ZERO_UUID}`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(0);
});

test('agents detail — unknown id renders error card with chrome intact', async ({ page }) => {
  await assertNotFoundCard(page, `/agents/${ZERO_UUID}`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(0);
});
