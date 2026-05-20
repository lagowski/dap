import { describe, it, expect } from "vitest";
import { buildGateAutoApproveRows, type ProjectFormValues } from "./project-form";
import type { Pipeline, ProjectCreate } from "@/lib/api/types";

describe("ProjectFormValues", () => {
  it("includes a pipelines field", () => {
    const values: ProjectFormValues = {
      name: "test",
      description: "desc",
      working_directory: null,
      repo_url: null,
      default_branch: "main",
      env_vars: {},
      pipelines: { cortex: "uuid-1" },
      auto_approve_nodes: [],
    };
    expect(values.pipelines).toEqual({ cortex: "uuid-1" });
  });

  it("round-trips cleanly with empty pipelines", () => {
    const values: ProjectFormValues = {
      name: "test",
      description: "",
      working_directory: null,
      repo_url: null,
      default_branch: "main",
      env_vars: {},
      pipelines: {},
      auto_approve_nodes: [],
    };
    expect(values.pipelines).toEqual({});
  });

  it("supports pre-populated edit state", () => {
    const values: ProjectFormValues = {
      name: "my-project",
      description: "desc",
      working_directory: "/var/work",
      repo_url: "https://github.com/org/repo.git",
      default_branch: "main",
      env_vars: {},
      pipelines: { deploy: "uuid-2", review: "uuid-3" },
      auto_approve_nodes: ["gate-phase2"],
    };
    expect(values.pipelines).toEqual({ deploy: "uuid-2", review: "uuid-3" });
    expect(values.auto_approve_nodes).toEqual(["gate-phase2"]);
  });

  it("is structurally compatible with ProjectCreate pipeline gate settings", () => {
    const formValues: ProjectFormValues = {
      name: "test",
      description: "",
      working_directory: null,
      repo_url: null,
      default_branch: "main",
      env_vars: {},
      pipelines: { cortex: "uuid-1" },
      auto_approve_nodes: ["gate-phase3"],
    };
    const payload: ProjectCreate = {
      name: formValues.name,
      pipelines: formValues.pipelines,
      auto_approve_nodes: formValues.auto_approve_nodes,
    };
    expect(payload.pipelines).toEqual({ cortex: "uuid-1" });
    expect(payload.auto_approve_nodes).toEqual(["gate-phase3"]);
  });

  it("builds gate auto-approval rows from bound pipelines and stale selections", () => {
    const pipeline = {
      id: "pipeline-1",
      name: "Cortex",
      defaults: {
        max_attempts: 3,
        budget_limit_usd: 1,
        approval_required_nodes: ["gate-phase2", "gate-phase3"],
      },
    } satisfies Pick<Pipeline, "id" | "name" | "defaults">;

    const rows = buildGateAutoApproveRows(
      { cortex: "pipeline-1" },
      ["legacy-gate"],
      new Map([[pipeline.id, pipeline]]),
    );

    expect(rows).toEqual([
      { nodeId: "gate-phase2", pipelineNames: ["Cortex"] },
      { nodeId: "gate-phase3", pipelineNames: ["Cortex"] },
      { nodeId: "legacy-gate", pipelineNames: [] },
    ]);
  });
});
