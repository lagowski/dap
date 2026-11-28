import { test, expect } from '../../../test-fixtures';

test('projects view — detail page shows seeded project fields', async ({ page, seedProject }) => {
  const project = await seedProject({ description: 'e2e seeded' });

  await page.goto(`/projects/${project.id}`);
  await expect(page.getByRole('heading', { name: project.name })).toBeVisible();
  await expect(page.getByText(project.default_branch)).toBeVisible();
  await expect(page.getByText(project.description)).toBeVisible();
});
