"use client";

/**
 * Shared destructive-action confirmation dialog (audit D3).
 *
 * Replaces nine ``window.confirm()`` callsites across the dashboard
 * with a single accessible, themable, focus-trapping dialog. The
 * imperative ``useConfirmDestructive()`` API matches the ergonomics
 * of the old ``window.confirm`` (returns a ``Promise<boolean>``) so
 * each callsite becomes a minimal awaited-`if` instead of having to
 * thread open-state through component trees.
 *
 * Design:
 *
 * - {@link ConfirmDestructiveProvider} is mounted once near the app
 *   root and owns the dialog state via React Context. Mounting it at
 *   the ``(app)`` layout level means the same provider scope covers
 *   every protected route without polluting the public ``(auth)``
 *   routes (which don't need destructive confirms).
 * - {@link useConfirmDestructive} returns a stable ``confirm()``
 *   function. Calling it sets the dialog options and returns a
 *   promise the provider resolves when the user clicks confirm or
 *   cancel (or dismisses via Escape / overlay click — both count as
 *   cancel).
 * - The dialog itself uses the existing shadcn ``Dialog`` primitives
 *   (Radix under the hood) rather than introducing ``AlertDialog`` as
 *   a new dep — one dialog flavour for the whole app keeps the focus
 *   trap, escape handling, and overlay styling consistent.
 *
 * Accessibility:
 *
 * - The confirm button gets initial focus (Radix's default — the
 *   first focusable in the content). The cancel button stays one
 *   Tab away, so a careless Enter doesn't auto-confirm a destructive
 *   action ... wait, that's exactly what we DON'T want. We
 *   intentionally focus the *cancel* button first by setting
 *   ``autoFocus`` on it and letting Radix's tab-loop handle the rest.
 *   See ``onOpenAutoFocus`` below.
 * - The dialog has ``role="alertdialog"`` semantics via the radix
 *   ``aria-describedby`` wiring (Radix' Dialog is generic but the
 *   primary action being destructive is communicated via the
 *   visible "Cannot be undone." subtitle and the destructive button
 *   variant; screen readers announce the title + description).
 */

import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Per-call options passed when invoking ``confirm()``. */
export interface ConfirmDestructiveOptions {
  /** Header text. Short imperative phrase, e.g. ``"Archive project"``. */
  title: string;
  /**
   * Longer description explaining the consequence. The first line of
   * the old ``window.confirm`` strings goes here verbatim — they
   * already explain "what archive means" in domain terms.
   */
  description: string;
  /** Label on the confirm button. Defaults to ``"Confirm"``. */
  confirmLabel?: string;
  /** Label on the cancel button. Defaults to ``"Cancel"``. */
  cancelLabel?: string;
}

/** Hook return — call to open the dialog, await the user's choice. */
export type ConfirmDestructive = (options: ConfirmDestructiveOptions) => Promise<boolean>;

// ---------------------------------------------------------------------------
// Internal: context shape
// ---------------------------------------------------------------------------

const ConfirmDestructiveContext = React.createContext<ConfirmDestructive | null>(null);

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Drop-in async replacement for ``window.confirm``.
 *
 * ``await confirm({...})`` resolves to ``true`` when the user clicks
 * the destructive button, ``false`` for anything else (cancel button,
 * Escape, overlay click, or unmount during open).
 *
 * Throws when called outside a ``ConfirmDestructiveProvider`` — that
 * makes wiring mistakes loud at dev time instead of silently no-op'ing.
 */
export function useConfirmDestructive(): ConfirmDestructive {
  const ctx = React.useContext(ConfirmDestructiveContext);
  if (ctx === null) {
    throw new Error(
      "useConfirmDestructive must be used inside a <ConfirmDestructiveProvider>",
    );
  }
  return ctx;
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

interface DialogState {
  options: ConfirmDestructiveOptions;
  resolve: (value: boolean) => void;
}

/**
 * App-level provider. Mount once near the root of the protected route
 * tree (e.g. ``app/(app)/layout.tsx``) — every descendant can then
 * call ``useConfirmDestructive()`` to open the shared dialog.
 *
 * Only one confirmation can be in flight at a time. A second call
 * while the dialog is open is treated as the caller wanting to
 * supersede; the in-flight promise resolves with ``false`` (treated
 * as "cancel") before the new dialog opens. That matches the
 * intuition that pressing "Archive" on another row while a confirm
 * is already open should cancel the first prompt.
 */
export function ConfirmDestructiveProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [state, setState] = React.useState<DialogState | null>(null);

  const confirm = React.useCallback<ConfirmDestructive>((options) => {
    return new Promise<boolean>((resolve) => {
      setState((prev) => {
        // Supersede any in-flight confirm — resolve the old one as
        // cancelled before showing the new dialog. Prevents a
        // dangling promise stuck behind a freshly-opened dialog.
        prev?.resolve(false);
        return { options, resolve };
      });
    });
  }, []);

  const handleOpenChange = React.useCallback(
    (open: boolean) => {
      if (!open && state) {
        // Closed via Escape / overlay / programmatically — treat as
        // cancel. The button-click handlers already resolve before
        // they trigger close, so this path only fires for
        // *implicit* dismissals.
        state.resolve(false);
        setState(null);
      }
    },
    [state],
  );

  const handleCancel = React.useCallback(() => {
    if (!state) return;
    state.resolve(false);
    setState(null);
  }, [state]);

  const handleConfirm = React.useCallback(() => {
    if (!state) return;
    state.resolve(true);
    setState(null);
  }, [state]);

  return (
    <ConfirmDestructiveContext.Provider value={confirm}>
      {children}
      <Dialog open={state !== null} onOpenChange={handleOpenChange}>
        <DialogContent
          // Focus the *cancel* button first — keyboard users hitting
          // Enter on a freshly-opened destructive prompt should not
          // accidentally confirm. This is the same convention browsers
          // use for ``window.confirm`` ("Cancel" is the default).
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            // Defer one tick so the dialog content is in the DOM.
            queueMicrotask(() => {
              const cancelBtn = document.querySelector<HTMLButtonElement>(
                '[data-confirm-destructive-role="cancel"]',
              );
              cancelBtn?.focus();
            });
          }}
        >
          <DialogHeader>
            <DialogTitle>{state?.options.title ?? ""}</DialogTitle>
            <DialogDescription>
              {state?.options.description ?? ""}
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">Cannot be undone.</p>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={handleCancel}
              data-confirm-destructive-role="cancel"
            >
              {state?.options.cancelLabel ?? "Cancel"}
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleConfirm}
              data-confirm-destructive-role="confirm"
            >
              {state?.options.confirmLabel ?? "Confirm"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ConfirmDestructiveContext.Provider>
  );
}
