"use client";

/**
 * Error boundary for authenticated pages — runs, agents, pipelines,
 * projects, admin, settings, designer, etc.
 *
 * Next.js App Router wraps every page in this segment with a React
 * error boundary that mounts this component when a render or data-
 * fetch error escapes the page. Without it, an uncaught error would
 * bubble to the root and unmount the whole shell (sidebar included),
 * leaving the operator on a white screen.
 *
 * The sidebar and ``ActiveProjectProvider`` from ``(app)/layout.tsx``
 * stay mounted because this boundary lives *inside* the layout tree —
 * so the user can still navigate elsewhere instead of having to reload.
 *
 * Audit finding D2 — see
 * ``.claude/plans/chce-zebys-zrobil-pelny-snuggly-unicorn.md``.
 */

import { useEffect } from "react";

import { Button } from "@/components/ui/button";

export default function AppError({
  error,
  reset,
}: {
  // ``digest`` is Next's hash of the error stack — useful to grep
  // server logs for the matching entry without leaking the message.
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Console-log only — sending to a remote sink (Sentry, etc.) lives
    // in a follow-up. The digest is enough to correlate with the
    // server-side log if the error originated in a Server Action.
    console.error("App segment error:", error);
  }, [error]);

  return (
    <div
      role="alert"
      className="flex h-full items-center justify-center bg-muted/30 p-8"
    >
      <div className="max-w-md space-y-4 rounded-lg border bg-background p-6 shadow-sm">
        <h2 className="text-lg font-semibold">Something went wrong</h2>
        <p className="text-sm text-muted-foreground">
          This page crashed while rendering. The rest of the dashboard
          is still working — use the sidebar to navigate elsewhere, or
          try again with the button below.
        </p>
        {error.digest ? (
          <p className="font-mono text-xs text-muted-foreground">
            Error ref: <span className="select-all">{error.digest}</span>
          </p>
        ) : null}
        <div className="flex gap-2">
          {/* Wrap ``reset`` in an arrow function so React's MouseEvent
              isn't passed through to a ``() => void`` callback —
              defensive in case Next's contract ever inspects args
              (Gemini strict review, #441 round 2). */}
          <Button onClick={() => reset()}>Try again</Button>
          <Button variant="outline" onClick={() => window.location.reload()}>
            Reload page
          </Button>
        </div>
      </div>
    </div>
  );
}
