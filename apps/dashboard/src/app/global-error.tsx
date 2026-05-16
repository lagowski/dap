"use client";

/**
 * Last-resort error boundary — fires only when an error escapes the
 * root layout itself. Next.js mounts this in place of the whole
 * ``<html>`` tree, so the JSX *must* include ``<html>`` + ``<head>`` +
 * ``<body>`` (the normal root layout doesn't run). Same constraint
 * applies to the styling — no provider tree (QueryProvider, theme,
 * etc.) so we lean on a minimal inline layout the user can still
 * read without globals.css loaded.
 *
 * In practice this is virtually never reached — the per-segment
 * ``(app)/error.tsx`` and ``(auth)/error.tsx`` catch ~all real errors.
 * This file exists so a layout-level crash doesn't drop the user on a
 * Next.js default error page.
 *
 * Implementation note: only one recovery button. Per Next.js docs the
 * ``reset`` prop on ``global-error`` performs a full document reload
 * (no router context is available at the root) — so we wire the
 * button to ``reset()`` rather than ``window.location.reload()``.
 * Functionally identical today, but using ``reset`` honours the
 * framework contract and would inherit any future Next-level
 * recovery behaviour (e.g. cache invalidation before the reload).
 *
 * Theming: ``Canvas`` / ``CanvasText`` are CSS system colors that
 * auto-respect the OS light/dark preference without needing
 * ``globals.css``. ``colorScheme: "light dark"`` opts the document
 * into respecting both schemes so the browser doesn't force a white
 * background under prefers-color-scheme: dark.
 *
 * Audit finding D2 — see
 * ``.claude/plans/chce-zebys-zrobil-pelny-snuggly-unicorn.md``.
 */

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Global (root) error:", error);
  }, [error]);

  return (
    <html lang="en" style={{ colorScheme: "light dark" }}>
      <head>
        {/* ``global-error`` *replaces* the entire ``<html>`` tree, so
            none of the inherited tags from the root layout apply. Re-
            declare the essentials by hand:
            - ``charset`` so the browser doesn't fall back to guessing
              the encoding (Gemini strict review, #441 round 4).
            - ``viewport`` so mobile renders at device width instead of
              simulated desktop (round 3). */}
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Dashboard error — DAP</title>
      </head>
      {/* Layout constraints live on an inner wrapper instead of
          ``<body>``: applying ``maxWidth`` to body would expose the
          html element's default background outside the centred column
          on wider viewports (Gemini strict review, #441 round 4). */}
      <body
        style={{
          backgroundColor: "Canvas",
          color: "CanvasText",
          fontFamily: "system-ui, -apple-system, sans-serif",
          margin: 0,
          lineHeight: 1.5,
        }}
      >
        <main
          style={{
            maxWidth: "640px",
            margin: "4rem auto",
            padding: "2rem",
          }}
        >
          <h1 style={{ fontSize: "1.25rem", margin: "0 0 1rem" }}>
            Dashboard failed to load
          </h1>
          <p style={{ opacity: 0.7, margin: "0 0 1rem" }}>
            The application could not render its root layout. This usually
            means the engine is unreachable or the build is broken.
          </p>
          {error.digest ? (
            <p
              style={{
                fontFamily: "ui-monospace, monospace",
                fontSize: "0.85rem",
                opacity: 0.6,
                margin: "0 0 1rem",
              }}
            >
              Error ref:{" "}
              {/* ``userSelect: all`` so a single click selects the
                  digest for copy-paste — matches the other two
                  boundaries' ``select-all`` Tailwind class. */}
              <span style={{ userSelect: "all" }}>{error.digest}</span>
            </p>
          ) : null}
          <button
            // Use Next's ``reset`` to honour the framework contract —
            // see file header (Gemini strict review, #441 round 3).
            onClick={() => reset()}
            style={{
              padding: "0.5rem 1rem",
              border: "1px solid CanvasText",
              borderRadius: "0.375rem",
              background: "Canvas",
              color: "CanvasText",
              cursor: "pointer",
            }}
          >
            Reload page
          </button>
        </main>
      </body>
    </html>
  );
}
