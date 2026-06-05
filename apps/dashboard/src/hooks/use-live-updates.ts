"use client";

/**
 * Live-updates preference hook (#662 Phase 2).
 *
 * Persists the user's "Live" auto-refresh choice for the run-detail
 * page to localStorage so it survives reloads. Defaults to ``true``
 * (auto-poll on) to preserve the pre-existing behaviour for users who
 * never touch the toggle.
 *
 * SSR-safe / hydration-safe: the initial state is the default (``true``)
 * so the server render and the first client render agree — no hydration
 * mismatch. The persisted choice is then applied in an effect after
 * mount (a one-frame "Live on" flash if the user had it off is fine for
 * this small, non-layout-shifting control). Mirrors the effect-based
 * localStorage pattern in ``lib/active-project.tsx``.
 */

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "dap:run-live-updates";

export function useLiveUpdates(): [boolean, (v: boolean) => void] {
  const [live, setLiveState] = useState<boolean>(true);

  // Apply the persisted preference after mount — never in the initialiser,
  // which would diverge from the server render and warn about a hydration
  // mismatch when the stored value is "false".
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored !== null) setLiveState(stored !== "false");
    } catch {
      // Private mode / quota — keep the default.
    }
  }, []);

  const setLive = useCallback((v: boolean) => {
    setLiveState(v);
    try {
      window.localStorage.setItem(STORAGE_KEY, v ? "true" : "false");
    } catch {
      // Same fallback as on read — in-memory only.
    }
  }, []);

  return [live, setLive];
}
