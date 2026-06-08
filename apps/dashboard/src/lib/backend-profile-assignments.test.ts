import { describe, expect, it } from "vitest";

import {
  addProfile,
  getDefaultProfile,
  getNodeOverride,
  listProfiles,
  profileSummary,
  removeProfile,
  resolveNodeProfile,
  setDefaultProfile,
  setNodeOverride,
  type BackendProfiles,
} from "./backend-profile-assignments";

const SAMPLE: BackendProfiles = {
  available: {
    anthropic: {
      runtime_id: "api-call",
      runtime_config: { provider: "anthropic", model_id: "claude-sonnet-4-6" },
      label: "Claude Sonnet",
    },
    openai: { runtime_id: "api-call", runtime_config: { provider: "openai", model_id: "gpt-4o" } },
  },
  agent_assignments: { default_profile: "anthropic", overrides: { verify: "openai" } },
};

describe("readers", () => {
  it("lists profiles with label fallback to id", () => {
    expect(listProfiles(SAMPLE)).toEqual([
      { id: "anthropic", label: "Claude Sonnet" },
      { id: "openai", label: "openai" },
    ]);
  });

  it("returns the default and per-node override", () => {
    expect(getDefaultProfile(SAMPLE)).toBe("anthropic");
    expect(getNodeOverride(SAMPLE, "verify")).toBe("openai");
    expect(getNodeOverride(SAMPLE, "select_task")).toBeNull();
  });

  it("resolves a node to its override, else the default", () => {
    expect(resolveNodeProfile(SAMPLE, "verify")).toBe("openai"); // override
    expect(resolveNodeProfile(SAMPLE, "select_task")).toBe("anthropic"); // default
    expect(resolveNodeProfile({}, "x")).toBeNull(); // nothing set
  });

  it("handles null/empty input", () => {
    expect(listProfiles(null)).toEqual([]);
    expect(getDefaultProfile(undefined)).toBeNull();
  });
});

describe("mutators are immutable", () => {
  it("setNodeOverride assigns and clears (inherit)", () => {
    const assigned = setNodeOverride(SAMPLE, "select_task", "openai");
    expect(getNodeOverride(assigned, "select_task")).toBe("openai");
    expect(SAMPLE.agent_assignments?.overrides?.select_task).toBeUndefined(); // original untouched

    const cleared = setNodeOverride(assigned, "select_task", null);
    expect(getNodeOverride(cleared, "select_task")).toBeNull();
  });

  it("setDefaultProfile sets and clears", () => {
    expect(getDefaultProfile(setDefaultProfile(SAMPLE, "openai"))).toBe("openai");
    expect(getDefaultProfile(setDefaultProfile(SAMPLE, null))).toBeNull();
  });

  it("addProfile adds without touching others", () => {
    const next = addProfile(SAMPLE, "deepseek", {
      runtime_id: "api-call",
      runtime_config: { provider: "deepseek" },
    });
    expect(listProfiles(next).map((p) => p.id)).toContain("deepseek");
    expect(listProfiles(next).map((p) => p.id)).toContain("anthropic");
  });

  it("removeProfile drops the profile and every reference to it", () => {
    const next = removeProfile(SAMPLE, "openai");
    expect(listProfiles(next).map((p) => p.id)).toEqual(["anthropic"]);
    // The 'verify' override pointed at openai → it must be gone, not dangling.
    expect(getNodeOverride(next, "verify")).toBeNull();
  });

  it("removeProfile clears default when it pointed at the removed profile", () => {
    const next = removeProfile(SAMPLE, "anthropic");
    expect(getDefaultProfile(next)).toBeNull();
  });
});

describe("profileSummary", () => {
  it("formats runtime + provider + model", () => {
    expect(profileSummary(SAMPLE.available!.anthropic)).toBe(
      "api-call · anthropic claude-sonnet-4-6",
    );
  });

  it("tolerates a sparse profile", () => {
    expect(profileSummary({ runtime_id: "bash" })).toBe("bash");
    expect(profileSummary(null)).toBe("—");
  });
});
