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
 * - "Reload page" makes more sense than "navigate elsewhere" here;
 *   the user has no alternative routes that would help.
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
        <h2 className="text-lg font-semibold">Sign-in page error</h2>
        <p className="text-sm text-muted-foreground">
          Something went wrong loading this auth page. Try again, or
          reload the browser if the problem persists.
        </p>
        {error.digest ? (
          <p className="font-mono text-xs text-muted-foreground">
            Error ref: <span className="select-all">{error.digest}</span>
          </p>
        ) : null}
        <div className="flex gap-2">
          <Button onClick={reset}>Try again</Button>
          <Button variant="outline" onClick={() => window.location.reload()}>
            Reload page
          </Button>
        </div>
      </div>
    </div>
  );
}
