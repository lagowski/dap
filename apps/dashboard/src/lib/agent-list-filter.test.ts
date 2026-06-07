import { describe, expect, it } from "vitest";
import { applyManagedFilter } from "./agent-list-filter";

const cortex = (id: string) => ({
  id,
  runtime_id: "python-func",
  runtime_config: { callable_path: "cortex.nodes.mockup:run" },
});
const mine = (id: string) => ({
  id,
  runtime_id: "claude-code",
  runtime_config: {},
});

describe("applyManagedFilter (#739 slice 2)", () => {
  const items = [mine("a"), cortex("b"), mine("c"), cortex("d")];

  it("counts managed agents regardless of the toggle", () => {
    expect(applyManagedFilter(items, { hideManaged: false }).managedCount).toBe(2);
    expect(applyManagedFilter(items, { hideManaged: true }).managedCount).toBe(2);
  });

  it("keeps every agent when the toggle is off", () => {
    const { rows } = applyManagedFilter(items, { hideManaged: false });
    expect(rows.map((r) => r.id)).toEqual(["a", "b", "c", "d"]);
  });

  it("drops managed agents when the toggle is on", () => {
    const { rows } = applyManagedFilter(items, { hideManaged: true });
    expect(rows.map((r) => r.id)).toEqual(["a", "c"]);
  });

  it("reports zero managed for an all-hand-authored list", () => {
    expect(applyManagedFilter([mine("a"), mine("b")], { hideManaged: false }).managedCount).toBe(0);
  });
});
