import { test, expect } from '@playwright/test';

// The pipeline create page is a React Flow visual graph editor — exercising
// the full "drop nodes, draw edges, save" flow via Playwright is impractical
// and brittle. The "Hello world — bash echo" template is the cheapest
// user-facing path that goes end-to-end through POST /pipelines/import +
// router.push to the new edit page, and that's what we cover here. Full
// graph manipulation is left to component-level tests.

test('pipelines create — template import lands on /pipelines/<id>/edit', async ({ page }) => {
  await page.goto('/pipelines/new');
  // Two-step flow (#688): the page opens on a grouped template chooser. The
  // page itself has no <h1>; the chooser's prompt is the topmost heading.
  await expect(page.getByRole('heading', { name: 'How do you want to start?' })).toBeVisible();

  // Each template is a single row <button> whose accessible name starts with
  // the template name (followed by its tags and description). Anchoring the
  // regex at the start means a different template whose *description*
  // mentions "Hello world — bash echo" can't match.
  await page.getByRole('button', { name: /^Hello world — bash echo / }).click();

  await page.waitForURL(/\/pipelines\/[^/]+\/edit$/, { timeout: 15_000 });
  // The toolbar Name input (placeholder "My pipeline") is pre-filled with the
  // template's pipeline name once the import + redirect resolves.
  await expect(page.getByPlaceholder('My pipeline')).toHaveValue('Hello world pipeline');
});
