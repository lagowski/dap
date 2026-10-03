import { expect, type Page } from '@playwright/test';

/**
 * Confirm the dashboard's shared destructive-action dialog.
 *
 * Since #453 every destructive action (archive, abort, revoke, soft-delete…)
 * opens the in-page ``ConfirmDestructiveDialog`` (a Radix dialog) instead of
 * ``window.confirm()``. A ``page.once('dialog', …)`` handler never fires for
 * it, so specs written against the old native prompt silently left the dialog
 * open and the action unconfirmed.
 *
 * Worse, an open Radix dialog marks the rest of the page ``aria-hidden``, so a
 * follow-up "row is gone" assertion such as
 * ``expect(page.getByRole('link', { name })).toHaveCount(0)`` passes without
 * the action ever having happened. Waiting for the dialog to close here makes
 * that false positive impossible.
 *
 * ``confirmLabel`` is the per-callsite ``confirmLabel`` (e.g. "Archive",
 * "Abort", "Revoke", "Soft-delete"). Scoping to the dialog matters: the
 * trigger button on the page often carries the same label.
 */
export async function confirmDestructive(page: Page, confirmLabel: string): Promise<void> {
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Cannot be undone.', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: confirmLabel, exact: true }).click();
  await expect(dialog).toHaveCount(0);
}
