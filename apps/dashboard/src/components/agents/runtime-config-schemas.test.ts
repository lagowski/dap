import { describe, expect, it } from "vitest";

import {
  RUNTIME_SCHEMAS,
  defaultRuntimeConfig,
  pruneRuntimeConfig,
} from "./runtime-config-schemas";

// #922: the CLI code runtimes share a declarative git workspace config.
const GIT_KEYS = [
  "workspace",
  "branch",
  "base",
  "push",
  "force_with_lease",
  "token_env",
  // #923 guards
  "require_nonempty_diff",
  "append_only",
  "ancestry_guard",
];
const CLI_RUNTIMES = ["claude-code", "codex", "gemini-cli"];

const field = (runtime: string, key: string) =>
  RUNTIME_SCHEMAS[runtime].fields.find((f) => f.key === key);

describe("git workspace fields on CLI runtimes (#922)", () => {
  it.each(CLI_RUNTIMES)("%s declares every git key", (runtime) => {
    const keys = RUNTIME_SCHEMAS[runtime].fields.map((f) => f.key);
    expect(keys).toEqual(expect.arrayContaining(GIT_KEYS));
  });

  it.each(CLI_RUNTIMES)("%s keeps git keys on submit", (runtime) => {
    const config = { model_id: "m", branch: "feat/x", push: true };
    expect(pruneRuntimeConfig(runtime, config)).toEqual(config);
  });

  it.each(CLI_RUNTIMES)(
    "%s gets no git key from defaults, so selecting it changes nothing",
    (runtime) => {
      const defaults = defaultRuntimeConfig(runtime);
      for (const key of GIT_KEYS) expect(defaults).not.toHaveProperty(key);
    },
  );

  it("shows base and push only once a branch is set", () => {
    for (const key of ["base", "push"]) {
      const f = field("codex", key);
      expect(f?.visible?.({})).toBe(false);
      expect(f?.visible?.({ branch: "feat/x" })).toBe(true);
    }
  });

  it("shows force_with_lease only when push is on", () => {
    const f = field("codex", "force_with_lease");
    expect(f?.visible?.({ branch: "feat/x" })).toBe(false);
    expect(f?.visible?.({ branch: "feat/x", push: true })).toBe(true);
  });

  it("shows the history guards once a branch is set (#923)", () => {
    for (const key of ["require_nonempty_diff", "append_only"]) {
      const f = field("codex", key);
      expect(f?.kind).toBe("boolean");
      expect(f?.visible?.({})).toBe(false);
      expect(f?.visible?.({ branch: "feat/x" })).toBe(true);
    }
  });

  it("shows ancestry_guard only when push is on (#923)", () => {
    const f = field("codex", "ancestry_guard");
    expect(f?.kind).toBe("boolean");
    expect(f?.visible?.({ branch: "feat/x" })).toBe(false);
    expect(f?.visible?.({ branch: "feat/x", push: true })).toBe(true);
  });

  it("asks for the token's env var name, never the token", () => {
    const f = field("codex", "token_env");
    expect(f?.kind).toBe("text");
    expect(f?.label.toLowerCase()).toContain("env var");
  });
});
