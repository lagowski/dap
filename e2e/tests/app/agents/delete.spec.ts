import { test, expect } from '@playwright/test';
import { createAgent } from '../../../helpers/agents';

test('agents delete (archive) — gone from active list after confirming dialog', async ({
  page,
  request,
}) => {
  const agent = await createAgent(request);

  // Detail page is where the Archive flow lives, and it auto-redirects to
  // /agents on success — exactly the assertion target we need.
  await page.goto(`/agents/${agent.id}`);
  await expect(page.getByRole('heading', { name: agent.name })).toBeVisible();

  // The Archive button triggers window.confirm() — accept it before clicking.
  page.once('dialog', (dialog) => {
    dialog.accept().catch(() => {
      // dialogs can race with the next navigation; ignore late-accept errors
    });
  });
  await page.getByRole('button', { name: 'Archive' }).click();

  await page.waitForURL(/\/agents\/?$/, { timeout: 10_000 });
  await expect(page.getByRole('link', { name: agent.name })).toHaveCount(0);
});
