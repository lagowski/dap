"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

/**
 * Live "what is the user looking at" context handed to the assistant (#689
 * phase 2). A page publishes a small, **non-secret** description of the form /
 * pipeline it's editing; the assistant panel reads it and sends it with each
 * chat turn so advice is tailored to the in-progress config.
 *
 * Contract: names + shape + non-secret scalar values only. Never put API keys,
 * tokens, passwords, or env-var *values* in here. The backend redacts
 * secret-looking keys as defence-in-depth, but the page is the first line.
 */
export type AssistantPageContext = Record<string, unknown>;

interface PageContextValue {
  context: AssistantPageContext | null;
  setPageContext: (ctx: AssistantPageContext | null) => void;
}

const PageContext = createContext<PageContextValue | null>(null);

export function AssistantPageContextProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [context, setContext] = useState<AssistantPageContext | null>(null);
  const setPageContext = useCallback(
    (ctx: AssistantPageContext | null) => setContext(ctx),
    [],
  );
  const value = useMemo(
    () => ({ context, setPageContext }),
    [context, setPageContext],
  );
  return <PageContext.Provider value={value}>{children}</PageContext.Provider>;
}

export function useAssistantPageContext(): PageContextValue {
  const ctx = useContext(PageContext);
  if (ctx === null) {
    // Outside the provider (e.g. a page rendered in isolation in tests):
    // publishing is a no-op and nothing reads it.
    return { context: null, setPageContext: () => {} };
  }
  return ctx;
}

/**
 * Publish ``ctx`` to the assistant while the calling component is mounted, and
 * clear it on unmount. Re-publishes whenever the serialized content changes, so
 * a page can pass live form values without worrying about object identity.
 * Pass ``null`` to publish nothing.
 */
export function usePublishAssistantContext(ctx: AssistantPageContext | null): void {
  const { setPageContext } = useAssistantPageContext();
  // Serialize for a stable effect dependency — callers typically build a fresh
  // object every render, so we key off content, not reference identity.
  const serialized = ctx ? JSON.stringify(ctx) : null;
  useEffect(() => {
    setPageContext(serialized ? (JSON.parse(serialized) as AssistantPageContext) : null);
    return () => setPageContext(null);
  }, [serialized, setPageContext]);
}
