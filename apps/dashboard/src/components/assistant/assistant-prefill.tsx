"use client";

import { createContext, useCallback, useContext, useState } from "react";

/**
 * One-shot form prefill handed from the assistant to a target form (#689
 * slice 3). The assistant panel stashes `{target, values}` then navigates to
 * the target's page; the page consumes it once on mount and seeds the form.
 * Values are always editable and never auto-saved.
 */
export interface AssistantPrefill {
  target: string;
  values: Record<string, unknown>;
}

interface PrefillContextValue {
  setPrefill: (prefill: AssistantPrefill) => void;
  /** Return + clear the pending prefill if it matches `target`, else null. */
  consumePrefill: (target: string) => Record<string, unknown> | null;
}

const PrefillContext = createContext<PrefillContextValue | null>(null);

export function AssistantPrefillProvider({ children }: { children: React.ReactNode }) {
  const [, setPending] = useState<AssistantPrefill | null>(null);

  const setPrefill = useCallback((prefill: AssistantPrefill) => setPending(prefill), []);
  const consumePrefill = useCallback(
    (target: string) => {
      let consumed: Record<string, unknown> | null = null;
      setPending((cur) => {
        if (cur && cur.target === target) {
          consumed = cur.values;
          return null;
        }
        return cur;
      });
      return consumed;
    },
    [],
  );

  return (
    <PrefillContext.Provider value={{ setPrefill, consumePrefill }}>
      {children}
    </PrefillContext.Provider>
  );
}

export function useAssistantPrefill(): PrefillContextValue {
  const ctx = useContext(PrefillContext);
  if (ctx === null) {
    // Outside the provider (e.g. tests rendering a form in isolation): no-op.
    return { setPrefill: () => {}, consumePrefill: () => null };
  }
  return ctx;
}
