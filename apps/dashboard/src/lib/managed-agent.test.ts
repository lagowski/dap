import { describe, expect, it } from "vitest";
import { callablePathOf, isManagedAgent, managedAgentInfo } from "./managed-agent";

describe("managedAgentInfo (#739)", () => {
  it("classifies a cortex python-func agent as managed", () => {
    const info = managedAgentInfo({
      runtime_id: "python-func",
      runtime_config: { callable_path: "cortex.nodes.mockup:run" },
    });
    expect(info).toEqual({
      managed: true,
      kind: "cortex",
      callablePath: "cortex.nodes.mockup:run",
    });
    expect(isManagedAgent({
      runtime_id: "python-func",
      runtime_config: { callable_path: "cortex.dap_steps.human_gate:noop" },
    })).toBe(true);
  });

  it("does not classify a non-cortex python-func callable as managed", () => {
    const info = managedAgentInfo({
      runtime_id: "python-func",
      runtime_config: { callable_path: "myproject.steps:run" },
    });
    expect(info.managed).toBe(false);
    expect(info.kind).toBeNull();
    expect(info.callablePath).toBe("myproject.steps:run");
  });

  it("does not classify LLM/CLI runtimes as managed", () => {
    expect(
      isManagedAgent({ runtime_id: "claude-code", runtime_config: {} }),
    ).toBe(false);
    expect(
      isManagedAgent({
        runtime_id: "api-call",
        runtime_config: { provider: "anthropic", callable_path: "cortex.x:y" },
      }),
    ).toBe(false);
  });

  it("handles a missing or non-string callable_path", () => {
    expect(callablePathOf({ runtime_id: "python-func", runtime_config: {} })).toBeNull();
    expect(
      callablePathOf({ runtime_id: "python-func", runtime_config: { callable_path: 42 } }),
    ).toBeNull();
    expect(
      managedAgentInfo({ runtime_id: "python-func", runtime_config: {} }).managed,
    ).toBe(false);
  });
});
