import { test, expect } from '../../../test-fixtures';

test('pipelines list — seeded pipeline appears in the table', async ({ page, seedPipeline }) => {
  const pipeline = await seedPipeline();

  await page.goto('/pipelines');
  await expect(page.getByRole('heading', { name: 'Pipelines' })).toBeVisible();
  // Pipeline names render as plain text in a <td>, not as links, so we
  // anchor on the cell role instead of the link role used for agents.
  await expect(page.getByRole('cell', { name: pipeline.name })).toBeVisible();
});
