import { test, expect } from '@playwright/test';
import { createPipeline, uniquePipelineName } from '../../../helpers/pipelines';

test('pipelines edit — rename, save, new name reflected in list', async ({ page, request }) => {
  const pipeline = await createPipeline(request);
  const newName = uniquePipelineName('e2e-pipeline-renamed');

  await page.goto(`/pipelines/${pipeline.id}/edit`);
  // Toolbar uses a bare <Field><Input/></Field> wrapper without htmlFor/id,
  // so anchor on the placeholder ("My pipeline") which is stable per-form.
  const nameInput = page.getByPlaceholder('My pipeline');
  await expect(nameInput).toHaveValue(pipeline.name);
  await nameInput.fill(newName);

  // Save label is dynamic — "Save v<next>". Wait for the PUT round-trip so
  // we don't navigate away mid-save.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/api/pipelines') && r.request().method() === 'PUT',
    ),
    page.getByRole('button', { name: /^Save v\d+$/ }).click(),
  ]);

  // Edit stays on /<id>/edit after save (unlike agents which redirect to
  // detail). Verify the rename by going back to the list.
  await page.goto('/pipelines');
  await expect(page.getByRole('cell', { name: newName })).toBeVisible();
  await expect(page.getByRole('cell', { name: pipeline.name })).toHaveCount(0);
});
