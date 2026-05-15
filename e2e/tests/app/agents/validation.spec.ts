import { test, expect } from '@playwright/test';

test('agents create — prompt template missing <agent_prompt> tag shows validation error', async ({
  page,
}) => {
  await page.goto('/agents/new');
  await page.locator('input[name="name"]').fill('e2e-bad-template-agent');
  await page.selectOption('select[name="role"]', 'implementer');
  await page.selectOption('select[name="runtime_id"]', 'bash');
  // Plain string without the <agent_prompt> root element. The form's
  // Zod schema requires the substring "<agent_prompt" to be present
  // (see apps/dashboard/src/components/agents/agent-form.tsx).
  await page
    .locator('textarea[name="prompt_template"]')
    .fill('just some text without the required xml tag');

  await page.getByRole('button', { name: /^Create agent$/ }).click();

  await expect(
    page.getByText('Template should contain <agent_prompt> root element'),
  ).toBeVisible();
  // Form blocked client-side; URL doesn't change.
  await expect(page).toHaveURL(/\/agents\/new/);
});
