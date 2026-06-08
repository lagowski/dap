import { managedAgentInfo, type ManagedAgentLike } from "./managed-agent";

/**
 * Group agents into standalone (hand-authored) agents and *packages* — sets of
 * managed agents that are nodes of an externally-owned bundle (#739) and have no
 * individual meaning to the operator. Cortex is the first such package: dozens
 * of python-func nodes imported as one unit, so the list/palette should present
 * them as a single collapsible package rather than enumerating every node.
 *
 * Pure so the listing logic stays testable without mounting the page.
 */

export interface AgentPackage<T> {
  /** Managed family identifier (currently only cortex). */
  kind: "cortex";
  /** Human label for the package header, e.g. "Cortex". */
  label: string;
  /** Where the package comes from, shown as a subtitle. */
  source: string;
  /** The member agents, in their original order. */
  agents: T[];
}

const PACKAGE_META: Record<"cortex", { label: string; source: string }> = {
  cortex: { label: "Cortex", source: "cortex bundle" },
};

export function groupAgentsIntoPackages<T extends ManagedAgentLike>(
  items: readonly T[],
): { standalone: T[]; packages: AgentPackage<T>[] } {
  const standalone: T[] = [];
  const byKind = new Map<"cortex", T[]>();

  for (const agent of items) {
    const { kind } = managedAgentInfo(agent);
    if (kind === null) {
      standalone.push(agent);
    } else {
      const bucket = byKind.get(kind) ?? [];
      bucket.push(agent);
      byKind.set(kind, bucket);
    }
  }

  const packages: AgentPackage<T>[] = [...byKind.entries()].map(([kind, agents]) => ({
    kind,
    label: PACKAGE_META[kind].label,
    source: PACKAGE_META[kind].source,
    agents,
  }));

  return { standalone, packages };
}

/** Total number of agents bundled inside packages (for summary labels). */
export function packagedAgentCount<T>(packages: readonly AgentPackage<T>[]): number {
  return packages.reduce((sum, pkg) => sum + pkg.agents.length, 0);
}
