import { test, expect } from '../../../test-fixtures';
import { ADMIN_EMAIL } from '../../../fixtures';

// All five mutations on /admin/users — promote, demote, suspend,
// reactivate, soft-delete — plus the "Show soft-deleted" filter and
// the self-action guard. The fixture's seedAdminTargetUser registers a
// throwaway user via the engine directly so the admin's cookie isn't
// replaced, and cleans them up via DELETE /api/users/<id> in teardown.

test('admin users — promote regular user → role flips to admin', async ({
  page,
  seedAdminTargetUser,
}) => {
  const target = await seedAdminTargetUser();

  await page.goto('/admin/users');
  const row = page.getByRole('row').filter({ hasText: target.email });
  await expect(row).toBeVisible();

  // Initial state: button is "Promote …"; after click it becomes "Demote …"
  await row.getByRole('button', { name: `Promote ${target.email} to admin` }).click();
  await expect(
    row.getByRole('button', { name: `Demote ${target.email} to member` }),
  ).toBeVisible();
});

test('admin users — demote admin → role flips back to member', async ({
  page,
  request,
  seedAdminTargetUser,
}) => {
  const target = await seedAdminTargetUser();
  // Seed as already-admin via the admin PATCH endpoint so the test
  // starts in "Demote" state rather than running the promote step first.
  const promote = await request.patch(`/api/users/${target.id}`, {
    data: { is_superuser: true },
  });
  expect(promote.ok()).toBe(true);

  await page.goto('/admin/users');
  const row = page.getByRole('row').filter({ hasText: target.email });
  await row.getByRole('button', { name: `Demote ${target.email} to member` }).click();
  await expect(
    row.getByRole('button', { name: `Promote ${target.email} to admin` }),
  ).toBeVisible();
});

test('admin users — suspend active user → status flips to suspended', async ({
  page,
  seedAdminTargetUser,
}) => {
  const target = await seedAdminTargetUser();

  await page.goto('/admin/users');
  const row = page.getByRole('row').filter({ hasText: target.email });
  await row.getByRole('button', { name: `Suspend ${target.email}` }).click();
  await expect(
    row.getByRole('button', { name: `Reactivate ${target.email}` }),
  ).toBeVisible();
});

test('admin users — soft-delete drops row from default list and Show toggle brings it back', async ({
  page,
  seedAdminTargetUser,
}) => {
  const target = await seedAdminTargetUser();

  await page.goto('/admin/users');
  const row = page.getByRole('row').filter({ hasText: target.email });
  await expect(row).toBeVisible();

  // Soft-delete fires window.confirm() — accept before clicking.
  page.once('dialog', (d) => {
    d.accept().catch(() => {
      // dialog races with the subsequent re-render
    });
  });
  await row.getByRole('button', { name: `Soft-delete ${target.email}` }).click();

  // Row vanishes from the default (active-only) listing.
  await expect(page.getByRole('row').filter({ hasText: target.email })).toHaveCount(0);

  // Toggle "Show soft-deleted" — row reappears.
  await page.getByLabel('Show soft-deleted').check();
  await expect(page.getByRole('row').filter({ hasText: target.email })).toBeVisible();
});

test('admin users — self-action guards: own row buttons are disabled', async ({ page }) => {
  await page.goto('/admin/users');
  const ownRow = page.getByRole('row').filter({ hasText: ADMIN_EMAIL });
  await expect(ownRow).toBeVisible();

  // Three icon-only action buttons swap to "You can't …" aria-labels +
  // disabled state when the row is the current user's own.
  await expect(
    ownRow.getByRole('button', { name: "You can't change your own role here" }),
  ).toBeDisabled();
  await expect(
    ownRow.getByRole('button', { name: "You can't suspend yourself" }),
  ).toBeDisabled();
  await expect(
    ownRow.getByRole('button', { name: "You can't soft-delete yourself" }),
  ).toBeDisabled();
});
