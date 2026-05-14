import { test, expect } from '@playwright/test';
import { uniqueAgentName } from '../../../helpers/agents';

// The AgentForm uses bare <Label>/<Input> pairs without htmlFor/id wiring, so
// page.getByLabel() can't link them. Anchor on the underlying name attribute
// (set by react-hook-form's form.register()) instead — that's the contract
// the form binds, and the inputs are native <input>/<select>/<textarea>.

test('agents create — fill form, submit, agent appears in list', async ({ page }) => {
  const name = uniqueAgentName();

  await page.goto('/agents/new');
  await expect(page.getByRole('heading', { name: 'New agent' })).toBeVisible();

  await page.locator('input[name="name"]').fill(name);
  await page.selectOption('select[name="role"]', 'implementer');
  await page.selectOption('select[name="runtime_id"]', 'bash');
  await page
    .locator('textarea[name="prompt_template"]')
    .fill('<agent_prompt>echo ok</agent_prompt>');

  await page.getByRole('button', { name: /^Create agent$/ }).click();

  await page.waitForURL(/\/agents\/?$/, { timeout: 10_000 });
  await expect(page.getByRole('link', { name })).toBeVisible();
});
