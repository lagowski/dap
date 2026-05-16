"use client";

/**
 * Error boundary for the unauthenticated pages — login, signup, reset
 * password, post-OAuth callback handler.
 *
 * Separate from the ``(app)/error.tsx`` boundary because the auth
 * routes have no sidebar / project context to keep mounted — the
 * styling is centred-card, full-screen, and matches the auth
 * layout instead of the in-app shell.
 *
 * Recovery paths:
 *
 * - ``handleRetry`` pairs ``router.refresh()`` with ``reset()`` so a
 *   Server-Component-side error (e.g. server-side OAuth code
 *   verification failing) actually gets re-fetched. ``reset()`` alone
 *   only re-renders Client Components, leaving cached server errors
 *   in place.
 *
 * - ``handleBackToLogin`` is the secondary action — visible only when
 *   the user is NOT already on /login. It navigates via
 *   ``router.push("/login")`` (respects Next ``basePath``; a hardcoded
 *   ``location.href`` would 404 under e.g. ``/dap/``) and then calls
 *   ``reset()`` because the auth-segment boundary is *shared* across
 *   login/signup/reset/OAuth-callback. Next does not remount a shared
 *   boundary on intra-segment navigation, so without ``reset()`` the
 *   URL would update but the error screen would stay painted.
 *
 * A plain ``window.location.reload()`` is intentionally avoided in
 * this segment: it would resubmit single-use query params (an OAuth
 * ``?code=...`` for instance) and trigger a secondary "code already
 * used" failure.
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
  // Strip a trailing slash before comparing so the check survives
  // ``trailingSlash: true`` in next.config.ts.
  const onLogin = (pathname ?? "").replace(/\/$/, "") === "/login";

  useEffect(() => {
    console.error("Auth segment error:", error);
  }, [error]);

  const handleRetry = () => {
    startTransition(() => {
      router.refresh();
      reset();
    });
  };

  // Only invoked from the "Back to sign-in" button, which is hidden
  // when ``onLogin`` is true — so we never need a refresh branch here.
  const handleBackToLogin = () => {
    startTransition(() => {
      router.push("/login");
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
