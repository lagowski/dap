"use client";

/**
 * Error boundary for the unauthenticated pages — login, signup, reset
 * password, post-OAuth callback handler.
 *
 * Separate from the ``(app)/error.tsx`` boundary because:
 *
 * - These pages have no sidebar / project context to keep mounted —
 *   the styling is centred-card, full-screen, and matches the auth
 *   layout instead of the in-app shell.
 * - The secondary recovery action navigates back to ``/login`` via
 *   ``window.location.href`` rather than ``router.push`` or a plain
 *   reload. Two constraints intersect here:
 *
 *   1. A plain ``window.location.reload()`` would resubmit any
 *      single-use query params (e.g. an OAuth ``?code=...``) and
 *      trigger a secondary "code already used" failure.
 *   2. ``router.push("/login")`` is a no-op in the App Router when
 *      the current pathname is already ``/login`` — so a crash *on*
 *      the login page itself would leave the button dead.
 *
 *   ``location.href = "/login"`` forces a full remount of the login
 *   route from any auth-segment page, dodging both pitfalls.
 *
 * Audit finding D2 — see
 * ``.claude/plans/chce-zebys-zrobil-pelny-snuggly-unicorn.md``.
 */

import { useEffect } from "react";

import { Button } from "@/components/ui/button";

export default function AuthError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Auth segment error:", error);
  }, [error]);

  return (
    <div
      role="alert"
      className="flex min-h-screen items-center justify-center bg-muted/30 p-6"
    >
      <div className="w-full max-w-md space-y-4 rounded-lg border bg-background p-6 shadow-sm">
        <h2 className="text-lg font-semibold">Authentication error</h2>
        <p className="text-sm text-muted-foreground">
          Something went wrong loading this page. Try again, or restart
          the sign-in flow if the problem persists.
        </p>
        {error.digest ? (
          <p className="font-mono text-xs text-muted-foreground">
            Error ref: <span className="select-all">{error.digest}</span>
          </p>
        ) : null}
        <div className="flex gap-2">
          {/* Wrap ``reset`` in an arrow function so React's MouseEvent
              isn't passed through to a ``() => void`` callback —
              defensive in case Next's contract ever inspects args. */}
          <Button onClick={() => reset()}>Try again</Button>
          {/* Force a full-page navigation rather than ``router.push`` —
              ``push`` to the current pathname is a no-op in Next.js
              App Router, so when this boundary fires *on* /login the
              button would be unrecoverably dead. ``location.href``
              forces a remount regardless (Gemini strict review,
              #441 round 3). */}
          <Button
            variant="outline"
            onClick={() => {
              window.location.href = "/login";
            }}
          >
            Back to sign-in
          </Button>
        </div>
      </div>
    </div>
  );
}
