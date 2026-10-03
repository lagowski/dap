import { test, expect } from '@playwright/test';
import { createAgent } from '../../../helpers/agents';
import { confirmDestructive } from '../../../helpers/confirm';

test('agents delete (archive) — gone from active list after confirming dialog', async ({
  page,
  request,
}) => {
  const agent = await createAgent(request);

  // Detail page is where the Archive flow lives, and it auto-redirects to
  // /agents on success — exactly the assertion target we need.
  await page.goto(`/agents/${agent.id}`);
  await expect(page.getByRole('heading', { name: agent.name })).toBeVisible();

  // Archive opens the in-page ConfirmDestructiveDialog, whose confirm
  // button is also labelled "Archive".
  await page.getByRole('button', { name: 'Archive' }).click();
  await confirmDestructive(page, 'Archive');

  await page.waitForURL(/\/agents\/?$/, { timeout: 10_000 });
  await expect(page.getByRole('link', { name: agent.name })).toHaveCount(0);
});
