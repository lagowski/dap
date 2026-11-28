import { test, expect } from '@playwright/test';
import { createAgent } from '../../../helpers/agents';

test('agents view — detail page shows seeded agent fields', async ({ page, request }) => {
  const agent = await createAgent(request);

  await page.goto(`/agents/${agent.id}`);
  await expect(page.getByRole('heading', { name: agent.name })).toBeVisible();
  await expect(page.getByText(agent.role)).toBeVisible();
  await expect(page.getByText(agent.runtime_id)).toBeVisible();
  await expect(page.getByText(agent.prompt_template)).toBeVisible();
});
