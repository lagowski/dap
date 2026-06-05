"use client";

/**
 * Live-updates preference hook (#662 Phase 2).
 *
 * Persists the user's "Live" auto-refresh choice for the run-detail
 * page to localStorage so it survives reloads. Defaults to ``true``
 * (auto-poll on) to preserve the pre-existing behaviour for users who
 * never touch the toggle.
 *
 * SSR-safe: the initialiser and the writer both guard ``typeof
 * window`` so the hook is inert during Next.js server rendering. We
 * read synchronously in the ``useState`` initialiser (rather than in an
 * effect) — the toggle is a small, non-layout-shifting control, so a
 * one-frame flash isn't a concern the way it is for the project picker.
 *
 * Follows the localStorage pattern in ``lib/active-project.tsx``.
 */

import { useCallback, useState } from "react";

const STORAGE_KEY = "dap:run-live-updates";

function readInitial(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === null) return true;
    return stored !== "false";
  } catch {
    // Private mode / quota — fall back to the default.
    return true;
  }
}

export function useLiveUpdates(): [boolean, (v: boolean) => void] {
  const [live, setLiveState] = useState<boolean>(readInitial);

  const setLive = useCallback((v: boolean) => {
    setLiveState(v);
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(STORAGE_KEY, v ? "true" : "false");
    } catch {
      // Same fallback as on read — in-memory only.
    }
  }, []);

  return [live, setLive];
}
