import { describe, expect, it } from "vitest";

import {
  AGENT_RUNTIME_IDS,
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

describe("github runtime (#920)", () => {
  const schema = RUNTIME_SCHEMAS.github;
  const gh = (key: string) => schema?.fields.find((f) => f.key === key);

  it("is offered as an agent runtime", () => {
    expect(AGENT_RUNTIME_IDS).toContain("github");
    expect(schema?.runtime_id).toBe("github");
  });

  it("offers the read ops and requires op and repo", () => {
    expect(gh("op")?.kind).toBe("select");
    expect(gh("op")?.options?.map((o) => o.value)).toEqual([
      "read_issue",
      "read_pr",
      "comment",
      "update_issue_section",
      "create_branch",
      "open_pr",
      "merge_pr",
    ]);
    expect(gh("op")?.required).toBe(true);
    expect(gh("repo")?.required).toBe(true);
  });

  // #921: each write op shows exactly the params it takes.
  const OP_FIELDS: Record<string, string[]> = {
    read_issue: ["issue"],
    read_pr: ["pr"],
    comment: ["issue", "body"],
    update_issue_section: ["issue", "section", "content"],
    create_branch: ["branch", "base"],
    open_pr: ["head", "base", "title", "body", "draft"],
    merge_pr: ["pr", "expected_head_sha", "method"],
  };
  const OP_PARAMS = [...new Set(Object.values(OP_FIELDS).flat())];

  it.each(Object.entries(OP_FIELDS))("%s shows exactly its params", (op, params) => {
    const shown = OP_PARAMS.filter((key) => gh(key)?.visible?.({ op }));
    expect(shown.sort()).toEqual([...params].sort());
  });

  it("explains that merge_pr needs the reviewed head sha", () => {
    expect(gh("expected_head_sha")?.description).toMatch(/head\.sha/);
    expect(gh("method")?.options?.map((o) => o.value)).toEqual(["squash", "merge", "rebase"]);
  });

  it("asks for the token's env var name, never a token", () => {
    expect(gh("token_env")?.label.toLowerCase()).toContain("env var");
    const keys = schema?.fields.map((f) => f.key) ?? [];
    expect(keys.some((k) => /^(token|password|secret)$/.test(k))).toBe(false);
  });

  it("keeps every github key on submit", () => {
    const config = {
      op: "read_pr",
      repo: "{{ state.repo }}",
      pr: "12",
      token_env: "CORTEX_GH_TOKEN_READ",
      state_key: "target",
      api_url: "https://ghe.example.com/api/v3",
    };
    expect(pruneRuntimeConfig("github", config)).toEqual(config);
  });
});
