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
 * (no router context is available at the root), so a separate "Reload
 * page" button would be functionally identical and confusing. We
 * expose just the reload action.
 *
 * Audit finding D2 — see
 * ``.claude/plans/chce-zebys-zrobil-pelny-snuggly-unicorn.md``.
 */

import { useEffect } from "react";

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
  // ``reset`` intentionally not destructured — see file header. Next
  // still passes it; we just don't expose it as a separate action.
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Global (root) error:", error);
  }, [error]);

  return (
    <html lang="en">
      <head>
        <title>Dashboard error — DAP</title>
      </head>
      <body
        // Inline ``backgroundColor`` + ``color`` to force a light
        // theme even if a browser extension flips the page background
        // dark when no stylesheet loads (Gemini strict review, #441
        // round 2).
        style={{
          backgroundColor: "#fff",
          color: "#000",
          fontFamily: "system-ui, -apple-system, sans-serif",
          padding: "2rem",
          maxWidth: "640px",
          margin: "4rem auto",
          lineHeight: 1.5,
        }}
      >
        <h1 style={{ fontSize: "1.25rem", margin: "0 0 1rem" }}>
          Dashboard failed to load
        </h1>
        <p style={{ color: "#555", margin: "0 0 1rem" }}>
          The application could not render its root layout. This usually
          means the engine is unreachable or the build is broken.
        </p>
        {error.digest ? (
          <p
            style={{
              fontFamily: "ui-monospace, monospace",
              fontSize: "0.85rem",
              color: "#777",
              margin: "0 0 1rem",
            }}
          >
            Error ref: <span>{error.digest}</span>
          </p>
        ) : null}
        <button
          onClick={() => window.location.reload()}
          style={{
            padding: "0.5rem 1rem",
            border: "1px solid #ccc",
            borderRadius: "0.375rem",
            background: "#fff",
            color: "#000",
            cursor: "pointer",
          }}
        >
          Reload page
        </button>
      </body>
    </html>
  );
}
