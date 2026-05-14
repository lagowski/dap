import { test, expect } from '@playwright/test';
import { uniqueProjectName } from '../../../helpers/projects';

test('projects create — fill form, submit, redirect to detail page', async ({ page }) => {
  const name = uniqueProjectName();

  await page.goto('/projects/new');
  await expect(page.getByRole('heading', { name: 'New project' })).toBeVisible();

  // ProjectForm uses react-hook-form's `register("...")` — same selector
  // strategy as AgentForm: anchor on the `name=` attribute that
  // form.register sets on each input. default_branch pre-fills to "main",
  // so name alone is enough to satisfy the Zod schema.
  await page.locator('input[name="name"]').fill(name);

  await page.getByRole('button', { name: 'Create project' }).click();

  // Form redirects to /projects/<id> on success.
  await page.waitForURL(/\/projects\/[^/]+$/, { timeout: 10_000 });
  await expect(page.getByRole('heading', { name })).toBeVisible();
});
