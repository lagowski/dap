import { describe, it, expect } from "vitest";
import { addBinding, updateBinding, removeBinding } from "./pipeline-bindings";

describe("addBinding", () => {
  it("adds a binding with placeholder kind to an empty map", () => {
    const result = addBinding({});
    expect(result).toEqual({ kind: "" });
  });

  it("avoids key collision with existing 'kind'", () => {
    const result = addBinding({ kind: "a" });
    expect(result).toEqual({ kind: "a", kind_1: "" });
  });

  it("increments placeholder when multiple collisions exist", () => {
    const result = addBinding({ kind: "a", kind_1: "b" });
    expect(result).toEqual({ kind: "a", kind_1: "b", kind_2: "" });
  });

  it("generates unique placeholder kind given existing bindings", () => {
    const result = addBinding({ cortex: "uuid-1" });
    expect(Object.keys(result)).toHaveLength(2);
    expect(result["cortex"]).toBe("uuid-1");
    const newKey = Object.keys(result).find((k) => k !== "cortex")!;
    expect(newKey).toBe("kind");
    expect(result[newKey]).toBe("");
  });
});

describe("updateBinding", () => {
  it("renames a kind key", () => {
    const result = updateBinding({ cortex: "uuid-1" }, "cortex", "deploy", "uuid-1");
    expect(result).toEqual({ deploy: "uuid-1" });
    expect("cortex" in result).toBe(false);
  });

  it("changes pipeline id without renaming", () => {
    const result = updateBinding(
      { cortex: "old-uuid" },
      "cortex",
      "cortex",
      "new-uuid",
    );
    expect(result).toEqual({ cortex: "new-uuid" });
  });

  it("preserves insertion order", () => {
    const result = updateBinding(
      { a: "1", b: "2", c: "3" },
      "b",
      "beta",
      "2",
    );
    expect(Object.keys(result)).toEqual(["a", "beta", "c"]);
  });

  it("removes entry when new kind is empty string", () => {
    const result = updateBinding({ cortex: "uuid-1" }, "cortex", "", "uuid-1");
    expect(result).toEqual({});
  });
});

describe("removeBinding", () => {
  it("deletes an entry", () => {
    const result = removeBinding(
      { cortex: "uuid-1", deploy: "uuid-2" },
      "cortex",
    );
    expect(result).toEqual({ deploy: "uuid-2" });
  });

  it("returns empty map when removing the only entry", () => {
    const result = removeBinding({ cortex: "uuid-1" }, "cortex");
    expect(result).toEqual({});
  });
});
