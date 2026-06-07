/**
 * Detection for "managed" agents (#739) — agents that aren't standalone,
 * hand-authored DAP agents but nodes of an inseparable, externally-owned
 * bundle. Cortex is the first (and currently only) kind: a python-func agent
 * whose callable lives in the ``dap-cortex`` package.
 *
 * A managed agent's prompts/logic are configured upstream (in the bundle's
 * package), not in DAP — so DAP surfaces them read-mostly: the prompt editor is
 * inert (#736), the dry-run tester executes the real callable with side
 * effects, and the role is a locked label. This helper centralizes the check so
 * every surface treats the category the same way.
 */

/** The minimal agent shape this module needs — anything with a runtime + config. */
export interface ManagedAgentLike {
  runtime_id: string;
  runtime_config: Record<string, unknown>;
}

export interface ManagedAgentInfo {
  /** True when the agent is a node of an externally-owned bundle. */
  managed: boolean;
  /** Which managed family it belongs to, or null when not managed. */
  kind: "cortex" | null;
  /** The ``module:function`` callable, when the runtime declares one. */
  callablePath: string | null;
}

/** Extract ``runtime_config.callable_path`` when it's a non-empty string. */
export function callablePathOf(agent: ManagedAgentLike): string | null {
  const raw = agent.runtime_config?.callable_path;
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

/**
 * Classify an agent's "managed" status. Cortex agents are python-func nodes
 * whose callable resolves into the ``cortex.`` package namespace.
 */
export function managedAgentInfo(agent: ManagedAgentLike): ManagedAgentInfo {
  const callablePath = callablePathOf(agent);
  const isCortex = agent.runtime_id === "python-func" && callablePath?.startsWith("cortex.") === true;
  return {
    managed: isCortex,
    kind: isCortex ? "cortex" : null,
    callablePath,
  };
}

/** Convenience boolean for call sites that only need the yes/no. */
export function isManagedAgent(agent: ManagedAgentLike): boolean {
  return managedAgentInfo(agent).managed;
}
