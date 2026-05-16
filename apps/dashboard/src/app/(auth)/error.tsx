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
 *   ``router.push`` + ``router.refresh`` rather than a plain reload
 *   or ``window.location.href``. Three constraints intersect:
 *
 *   1. A plain ``window.location.reload()`` would resubmit any
 *      single-use query params (e.g. an OAuth ``?code=...``) and
 *      trigger a secondary "code already used" failure.
 *   2. ``router.push("/login")`` alone is a no-op in the App Router
 *      when the current pathname is already ``/login`` — so a crash
 *      *on* the login page itself would leave the button dead.
 *   3. ``window.location.href = "/login"`` ignores Next.js
 *      ``basePath`` config — self-hosted deployments under
 *      e.g. ``/dap/`` would 404.
 *
 *   ``router.push + router.refresh`` respects ``basePath`` AND forces
 *   a server re-render even on the same path, dodging all three
 *   pitfalls (Gemini strict review, #441 round 3).
 * - ``handleRetry`` pairs ``router.refresh()`` with ``reset()`` so
 *   a Server-Component-side error (e.g. server-side OAuth code
 *   verification failing) actually gets re-fetched. ``reset()`` alone
 *   only re-renders Client Components.
 *
 * Audit finding D2 — see
 * ``.claude/plans/chce-zebys-zrobil-pelny-snuggly-unicorn.md``.
 */

import { usePathname, useRouter } from "next/navigation";
import { startTransition, useEffect } from "react";

import { Button } from "@/components/ui/button";

export default function AuthError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const onLogin = pathname === "/login";

  useEffect(() => {
    console.error("Auth segment error:", error);
  }, [error]);

  /**
   * Recover from an error possibly caused by Server Component work
   * (e.g. server-side OAuth-code verification). Mirrors the pattern
   * in ``(app)/error.tsx``: ``router.refresh()`` invalidates the
   * route cache + re-runs server render, then ``reset()`` re-mounts
   * the error boundary so the now-healthy tree can take over
   * (Gemini strict review, #441 round 3).
   */
  const handleRetry = () => {
    startTransition(() => {
      router.refresh();
      reset();
    });
  };

  /**
   * Secondary action: drop the user on /login regardless of which
   * auth route crashed. The router action is chosen by where we are:
   *   - already on /login → ``router.refresh()`` (a push to the
   *     current pathname is a no-op, and dispatching push + refresh
   *     into the same transition wastes work in the router reducer
   *     — Gemini strict review, #441 round 5).
   *   - any other auth route → ``router.push("/login")`` (respects
   *     Next ``basePath``; a hardcoded ``location.href`` wouldn't).
   * Either branch ends with ``reset()`` because the auth-segment
   * boundary is *shared* across login/signup/reset/OAuth-callback —
   * Next won't remount it on intra-segment navigation, so without
   * ``reset()`` the URL updates but the error stays painted (round 4).
   */
  const handleBackToLogin = () => {
    startTransition(() => {
      if (onLogin) {
        router.refresh();
      } else {
        router.push("/login");
      }
      reset();
    });
  };

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
          <Button onClick={handleRetry}>Try again</Button>
          {/* Hide "Back to sign-in" when the user is already on
              /login — "Try again" already covers that path. Showing
              two buttons that do the same thing is confusing UX
              (Gemini strict review, #441 round 5). */}
          {onLogin ? null : (
            <Button variant="outline" onClick={handleBackToLogin}>
              Back to sign-in
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
