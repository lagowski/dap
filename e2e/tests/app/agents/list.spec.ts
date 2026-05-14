import { test, expect } from '@playwright/test';
import { createAgent } from '../../../helpers/agents';

test('agents list — seeded agent appears in the table', async ({ page, request }) => {
  const agent = await createAgent(request);

  await page.goto('/agents');
  await expect(page.getByRole('heading', { name: 'Agents' })).toBeVisible();
  await expect(page.getByRole('link', { name: agent.name })).toBeVisible();
});
