import { describe, expect, it } from "vitest";

import { groupAgentsIntoPackages, packagedAgentCount } from "./agent-packages";

const standalone = (name: string) => ({
  name,
  runtime_id: "api-call",
  runtime_config: {},
});

const cortex = (name: string) => ({
  name,
  runtime_id: "python-func",
  runtime_config: { callable_path: `cortex.phase1.${name}` },
});

describe("groupAgentsIntoPackages", () => {
  it("keeps hand-authored agents standalone and has no packages", () => {
    const { standalone: solo, packages } = groupAgentsIntoPackages([
      standalone("alpha"),
      standalone("beta"),
    ]);
    expect(solo.map((a) => a.name)).toEqual(["alpha", "beta"]);
    expect(packages).toEqual([]);
  });

  it("collapses cortex agents into a single Cortex package", () => {
    const { standalone: solo, packages } = groupAgentsIntoPackages([
      standalone("mine"),
      cortex("dispatch"),
      cortex("finalize"),
      cortex("verify"),
    ]);
    expect(solo.map((a) => a.name)).toEqual(["mine"]);
    expect(packages).toHaveLength(1);
    expect(packages[0].kind).toBe("cortex");
    expect(packages[0].label).toBe("Cortex");
    expect(packages[0].source).toBe("cortex bundle");
    expect(packages[0].agents.map((a) => a.name)).toEqual(["dispatch", "finalize", "verify"]);
  });

  it("preserves original order within a package", () => {
    const { packages } = groupAgentsIntoPackages([cortex("c"), cortex("a"), cortex("b")]);
    expect(packages[0].agents.map((a) => a.name)).toEqual(["c", "a", "b"]);
  });

  it("emits no package when there are no managed agents", () => {
    const { packages } = groupAgentsIntoPackages([standalone("only")]);
    expect(packages).toEqual([]);
  });
});

describe("packagedAgentCount", () => {
  it("sums member counts across packages", () => {
    const { packages } = groupAgentsIntoPackages([
      cortex("a"),
      cortex("b"),
      standalone("x"),
    ]);
    expect(packagedAgentCount(packages)).toBe(2);
  });

  it("is zero for no packages", () => {
    expect(packagedAgentCount([])).toBe(0);
  });
});
