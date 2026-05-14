import { test, expect } from '../../../test-fixtures';
import { uniqueProjectName } from '../../../helpers/projects';

test('projects edit — rename, save, new name reflected on detail page', async ({
  page,
  seedProject,
}) => {
  const project = await seedProject();
  const newName = uniqueProjectName('e2e-project-renamed');

  await page.goto(`/projects/${project.id}/edit`);
  const nameInput = page.locator('input[name="name"]');
  await expect(nameInput).toHaveValue(project.name);
  await nameInput.fill(newName);

  await page.getByRole('button', { name: 'Save' }).click();

  // Save redirects back to /projects/<id> (the detail page).
  await page.waitForURL(new RegExp(`/projects/${project.id}/?$`), { timeout: 10_000 });
  await expect(page.getByRole('heading', { name: newName })).toBeVisible();
});
