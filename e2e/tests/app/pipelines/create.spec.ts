import { test, expect } from '@playwright/test';

// The pipeline create page is a React Flow visual graph editor — exercising
// the full "drop nodes, draw edges, save" flow via Playwright is impractical
// and brittle. The "Hello world — bash echo" template is the cheapest
// user-facing path that goes end-to-end through POST /pipelines/import +
// router.push to the new edit page, and that's what we cover here. Full
// graph manipulation is left to component-level tests.

test('pipelines create — template import lands on /pipelines/<id>/edit', async ({ page }) => {
  await page.goto('/pipelines/new');
  // The page itself has no <h1>; the template picker is the topmost heading.
  await expect(page.getByRole('heading', { name: /Start from template/i })).toBeVisible();

  // Each template card renders the template name as a <div> plus a "Use
  // this template" <Button>. Scope by finding the deepest div that
  // *contains* an exact-match name element (so a different card whose
  // description coincidentally contains "Hello world — bash echo" as a
  // substring can't match) *and* an import button — that's the card.
  const card = page
    .locator('div')
    .filter({ has: page.getByText('Hello world — bash echo', { exact: true }) })
    .filter({ has: page.getByRole('button', { name: /Use this template/i }) });
  await card.last().getByRole('button', { name: /Use this template/i }).click();

  await page.waitForURL(/\/pipelines\/[^/]+\/edit$/, { timeout: 15_000 });
  // The toolbar Name input (placeholder "My pipeline") is pre-filled with the
  // template's pipeline name once the import + redirect resolves.
  await expect(page.getByPlaceholder('My pipeline')).toHaveValue('Hello world pipeline');
});
