import { isManagedAgent, type ManagedAgentLike } from "./managed-agent";

/**
 * Apply the "hide managed (Cortex) agents" list filter (#739 slice 2).
 *
 * Returns the rows to display plus the total count of managed agents (so the
 * toggle can label itself and hide itself when there are none). Pure — keeps
 * the listing logic testable without mounting the whole agents page.
 */
export function applyManagedFilter<T extends ManagedAgentLike>(
  items: readonly T[],
  options: { hideManaged: boolean },
): { rows: T[]; managedCount: number } {
  const managedCount = items.filter(isManagedAgent).length;
  const rows = options.hideManaged ? items.filter((a) => !isManagedAgent(a)) : [...items];
  return { rows, managedCount };
}
