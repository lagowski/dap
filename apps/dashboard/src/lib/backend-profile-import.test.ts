import { describe, expect, it } from "vitest";
import type { PipelineExport } from "@/lib/api/types";
import {
  bundleHasBackendProfiles,
  selectableDefaultProfile,
  withDefaultBackendProfile,
} from "./backend-profile-import";

const baseBundle: PipelineExport = {
  schema_version: "pipeline-export/2",
  pipeline: {
    name: "Bundle",
    description: "",
    schema_version: "langgraph/1.0",
    state_schema_ref: "PipelineState.v1",
    entry_point: "n1",
    nodes: [],
    edges: [],
    defaults: {
      max_attempts: 1,
      budget_limit_usd: 0,
      approval_required_nodes: [],
    },
  },
};

describe("backend profile import helpers", () => {
  it("detects bundles with an available profiles object", () => {
    expect(bundleHasBackendProfiles(baseBundle)).toBe(false);
    expect(
      bundleHasBackendProfiles({
        ...baseBundle,
        backend_profiles: { available: { claude: { label: "Claude" } } },
      }),
    ).toBe(true);
  });

  it("prefers the declared default when it exists", () => {
    expect(
      selectableDefaultProfile({
        default_profile: "deepseek",
        overrides: {},
        profiles: [
          { id: "claude", label: "Claude", available: true },
          { id: "deepseek", label: "DeepSeek", available: false },
        ],
      }),
    ).toBe("deepseek");
  });

  it("writes the selected default profile without dropping overrides", () => {
    const updated = withDefaultBackendProfile(
      {
        ...baseBundle,
        backend_profiles: {
          available: { claude: { label: "Claude" } },
          agent_assignments: {
            default_profile: "old",
            overrides: { coder: "claude" },
          },
        },
      },
      "deepseek",
    );

    expect(updated.backend_profiles).toEqual({
      available: { claude: { label: "Claude" } },
      agent_assignments: {
        default_profile: "deepseek",
        overrides: { coder: "claude" },
      },
    });
  });
});
