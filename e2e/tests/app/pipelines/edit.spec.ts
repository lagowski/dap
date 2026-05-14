import { test, expect } from '../../../test-fixtures';
import { uniquePipelineName } from '../../../helpers/pipelines';

test('pipelines edit — rename, save, new name reflected in list', async ({
  page,
  seedPipeline,
}) => {
  const pipeline = await seedPipeline();
  const newName = uniquePipelineName('e2e-pipeline-renamed');

  await page.goto(`/pipelines/${pipeline.id}/edit`);
  // Toolbar uses a bare <Field><Input/></Field> wrapper without htmlFor/id,
  // so anchor on the placeholder ("My pipeline") which is stable per-form.
  const nameInput = page.getByPlaceholder('My pipeline');
  await expect(nameInput).toHaveValue(pipeline.name);
  await nameInput.fill(newName);

  // Wait specifically for the PUT to *this* pipeline so unrelated PUTs
  // anywhere under /api/pipelines/... (sub-resources, validate calls, etc.)
  // can't satisfy the wait early.
  await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === 'PUT' &&
        r.url().endsWith(`/api/pipelines/${pipeline.id}`),
    ),
    page.getByRole('button', { name: /^Save v\d+$/ }).click(),
  ]);

  // Edit stays on /<id>/edit after save (unlike agents which redirect to
  // detail). Verify the rename by going back to the list.
  await page.goto('/pipelines');
  await expect(page.getByRole('cell', { name: newName })).toBeVisible();
  await expect(page.getByRole('cell', { name: pipeline.name })).toHaveCount(0);
});
