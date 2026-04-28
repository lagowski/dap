"use client";

/**
 * Active project context (#68).
 *
 * Lightweight React context — no external state lib. The active
 * project is persisted to localStorage so it survives reloads. We
 * hydrate on mount inside an effect (not during render) to keep
 * Next.js's server-rendered HTML in sync with the first client
 * render — otherwise the picker would briefly flash with the
 * "All projects" default before snapping to the persisted value.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const STORAGE_KEY = "dap.activeProjectId";

interface ActiveProjectContextValue {
  /** ``null`` means "All projects" — list pages show org-wide data. */
  activeProjectId: string | null;
  setActiveProjectId: (id: string | null) => void;
  /** True after the localStorage hydration effect has run. */
  isHydrated: boolean;
}

const ActiveProjectContext = createContext<ActiveProjectContextValue | null>(null);

export function ActiveProjectProvider({ children }: { children: ReactNode }) {
  const [activeProjectId, setActiveProjectIdState] = useState<string | null>(null);
  const [isHydrated, setIsHydrated] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored && stored !== "null") {
        setActiveProjectIdState(stored);
      }
    } catch {
      // Private mode / quota — fall back to in-memory only.
    }
    setIsHydrated(true);
  }, []);

  const setActiveProjectId = useCallback((id: string | null) => {
    setActiveProjectIdState(id);
    if (typeof window === "undefined") return;
    try {
      if (id === null) {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, id);
      }
    } catch {
      // Same fallback as on read.
    }
  }, []);

  const value = useMemo(
    () => ({ activeProjectId, setActiveProjectId, isHydrated }),
    [activeProjectId, setActiveProjectId, isHydrated],
  );

  return (
    <ActiveProjectContext.Provider value={value}>
      {children}
    </ActiveProjectContext.Provider>
  );
}

export function useActiveProject(): ActiveProjectContextValue {
  const ctx = useContext(ActiveProjectContext);
  if (ctx === null) {
    throw new Error(
      "useActiveProject must be used inside <ActiveProjectProvider>",
    );
  }
  return ctx;
}
