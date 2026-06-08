/**
 * Pure helpers for editing a pipeline's ``backend_profiles`` — the per-node
 * LLM/backend assignment layer the engine resolves at run time
 * (``resolve_backend_profile``: ``agent_assignments.overrides[node]`` →
 * ``default_profile`` → the node's own agent config).
 *
 * Shape (intentionally loose JSON for bundle compatibility):
 *   {
 *     available: { <id>: { runtime_id?, runtime_config?, label?, requires_env? } },
 *     agent_assignments: { default_profile?: string, overrides?: { <nodeId>: <id> } }
 *   }
 *
 * Every mutator returns a new object (immutable) so React state updates stay
 * predictable. ``null`` profile = "inherit default" (override removed).
 */

export interface BackendProfile {
  runtime_id?: string;
  runtime_config?: Record<string, unknown>;
  label?: string;
  requires_env?: string[];
  requires_service?: string;
}

export interface BackendProfiles {
  available?: Record<string, BackendProfile>;
  agent_assignments?: {
    default_profile?: string;
    overrides?: Record<string, string>;
  };
  // Loose JSON for bundle compatibility — also makes the type assignable to
  // ``Record<string, unknown>`` at the API/save boundary.
  [key: string]: unknown;
}

export interface ProfileOption {
  id: string;
  label: string;
}

const EMPTY: BackendProfiles = {};

export function listProfiles(bp: BackendProfiles | null | undefined): ProfileOption[] {
  const available = bp?.available;
  if (!available || typeof available !== "object") return [];
  return Object.entries(available).map(([id, profile]) => ({
    id,
    label: profile?.label?.trim() ? profile.label : id,
  }));
}

export function getDefaultProfile(bp: BackendProfiles | null | undefined): string | null {
  const value = bp?.agent_assignments?.default_profile;
  return typeof value === "string" && value ? value : null;
}

export function getNodeOverride(
  bp: BackendProfiles | null | undefined,
  nodeId: string,
): string | null {
  const value = bp?.agent_assignments?.overrides?.[nodeId];
  return typeof value === "string" && value ? value : null;
}

/** What a node actually runs with: its override, else the pipeline default,
 *  else null (the node falls back to its own agent's runtime config). */
export function resolveNodeProfile(
  bp: BackendProfiles | null | undefined,
  nodeId: string,
): string | null {
  return getNodeOverride(bp, nodeId) ?? getDefaultProfile(bp);
}

function withAssignments(
  bp: BackendProfiles | null | undefined,
  next: NonNullable<BackendProfiles["agent_assignments"]>,
): BackendProfiles {
  return { ...(bp ?? EMPTY), agent_assignments: next };
}

export function setDefaultProfile(
  bp: BackendProfiles | null | undefined,
  profileId: string | null,
): BackendProfiles {
  const assignments = { ...(bp?.agent_assignments ?? {}) };
  if (profileId) {
    assignments.default_profile = profileId;
  } else {
    delete assignments.default_profile;
  }
  return withAssignments(bp, assignments);
}

export function setNodeOverride(
  bp: BackendProfiles | null | undefined,
  nodeId: string,
  profileId: string | null,
): BackendProfiles {
  const overrides = { ...(bp?.agent_assignments?.overrides ?? {}) };
  if (profileId) {
    overrides[nodeId] = profileId;
  } else {
    delete overrides[nodeId];
  }
  return withAssignments(bp, {
    ...(bp?.agent_assignments ?? {}),
    overrides,
  });
}

export function addProfile(
  bp: BackendProfiles | null | undefined,
  id: string,
  profile: BackendProfile,
): BackendProfiles {
  return {
    ...(bp ?? EMPTY),
    available: { ...(bp?.available ?? {}), [id]: profile },
  };
}

/** Remove a profile and every reference to it (default + node overrides), so we
 *  never leave a dangling assignment pointing at a deleted profile. */
export function removeProfile(
  bp: BackendProfiles | null | undefined,
  id: string,
): BackendProfiles {
  const available = { ...(bp?.available ?? {}) };
  delete available[id];

  const assignments = { ...(bp?.agent_assignments ?? {}) };
  if (assignments.default_profile === id) delete assignments.default_profile;
  if (assignments.overrides) {
    const overrides = Object.fromEntries(
      Object.entries(assignments.overrides).filter(([, profileId]) => profileId !== id),
    );
    assignments.overrides = overrides;
  }

  return { ...(bp ?? EMPTY), available, agent_assignments: assignments };
}

/** One-line description of a profile for display, e.g.
 *  "api-call · anthropic claude-sonnet-4-6". */
export function profileSummary(profile: BackendProfile | undefined | null): string {
  if (!profile) return "—";
  const cfg = profile.runtime_config ?? {};
  const provider = typeof cfg.provider === "string" ? cfg.provider : null;
  const model =
    typeof cfg.model_id === "string"
      ? cfg.model_id
      : typeof cfg.model === "string"
        ? cfg.model
        : null;
  const parts = [profile.runtime_id, [provider, model].filter(Boolean).join(" ")].filter(
    (part) => part && String(part).trim(),
  );
  return parts.join(" · ") || "—";
}
