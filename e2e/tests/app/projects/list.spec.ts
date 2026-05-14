import { test, expect } from '../../../test-fixtures';

test('projects list — seeded project appears in the table', async ({ page, seedProject }) => {
  const project = await seedProject();

  await page.goto('/projects');
  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible();
  // Project name renders inside a <Link> to /projects/<id>.
  await expect(page.getByRole('link', { name: project.name })).toBeVisible();
});
