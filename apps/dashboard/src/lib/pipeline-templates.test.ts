import { describe, expect, it } from "vitest";
import { PIPELINE_TEMPLATES, PIPELINE_TEMPLATE_CATEGORIES } from "./pipeline-templates";

describe("PIPELINE_TEMPLATES integrity", () => {
  it("every node.agent_id resolves to a bundled agent (importer contract)", () => {
    // The importer remaps node.agent_id against bundled_agents keys; an
    // unbundled reference would 422 at import time. Guard every template.
    for (const t of PIPELINE_TEMPLATES) {
      const bundledKeys = new Set(Object.keys(t.bundle.bundled_agents ?? {}));
      for (const node of t.bundle.pipeline.nodes) {
        expect(
          bundledKeys.has(node.agent_id),
          `${t.id}: node "${node.id}" references unbundled agent "${node.agent_id}"`,
        ).toBe(true);
      }
    }
  });

  it("every template's category is a known category", () => {
    for (const t of PIPELINE_TEMPLATES) {
      expect(PIPELINE_TEMPLATE_CATEGORIES).toContain(t.category);
    }
  });

  it("template ids are unique", () => {
    const ids = PIPELINE_TEMPLATES.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("Cortex template (#706)", () => {
  const cortex = PIPELINE_TEMPLATES.find((t) => t.id === "cortex-github-issue");

  it("is present under the Cortex category", () => {
    expect(cortex).toBeDefined();
    expect(cortex!.category).toBe("Cortex");
  });

  it("is an all-python-func bundle (cortex nodes are deterministic callables)", () => {
    const agents = Object.values(cortex!.bundle.bundled_agents ?? {});
    expect(agents.length).toBeGreaterThan(0);
    for (const a of agents) {
      expect(a.runtime_id).toBe("python-func");
      // Every cortex node is a callable into the dap-cortex package.
      expect(a.runtime_config).toHaveProperty("callable_path");
    }
  });

  it("documents the dap-cortex precondition in its description", () => {
    expect(cortex!.description).toMatch(/dap-cortex/);
    expect(cortex!.description).toMatch(/CORTEX_/);
  });
});
