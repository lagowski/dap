import { test, expect } from '../../../test-fixtures';
import { uniqueProjectName } from '../../../helpers/projects';

test('projects create — fill form, submit, redirect to detail page', async ({
  page,
  trackResource,
}) => {
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

  // Register the newly-created project for fixture teardown so the UI flow
  // doesn't leave state behind for the rest of the run.
  const projectId = page.url().match(/\/projects\/([^/]+)$/)?.[1];
  if (projectId) trackResource('project', projectId);

  await expect(page.getByRole('heading', { name })).toBeVisible();
});
