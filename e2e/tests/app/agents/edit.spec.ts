import { test, expect } from '@playwright/test';
import { createAgent, uniqueAgentName } from '../../../helpers/agents';

test('agents edit — rename, save, new name reflected on detail page', async ({ page, request }) => {
  const agent = await createAgent(request);
  const newName = uniqueAgentName('e2e-agent-renamed');

  await page.goto(`/agents/${agent.id}/edit`);
  // Name input pre-fills with the agent's current name — proves we're on
  // the edit form for this agent without depending on heading text shape.
  const nameInput = page.locator('input[name="name"]');
  await expect(nameInput).toHaveValue(agent.name);

  await nameInput.fill(newName);

  // Submit button label is dynamic: "Save v<next>" where next = version + 1.
  await page.getByRole('button', { name: /^Save v\d+$/ }).click();

  await page.waitForURL(new RegExp(`/agents/${agent.id}/?$`), { timeout: 10_000 });
  await expect(page.getByRole('heading', { name: newName })).toBeVisible();
});
