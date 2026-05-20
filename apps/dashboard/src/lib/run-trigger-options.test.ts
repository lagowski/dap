import { describe, expect, it } from "vitest";

import { withAutoApprove } from "./run-trigger-options";

describe("withAutoApprove", () => {
  it("leaves initial state unchanged when disabled", () => {
    const state = { repo: "org/repo" };
    expect(withAutoApprove(state, false)).toBe(state);
  });

  it("adds extensions.auto_approve without dropping existing extensions", () => {
    expect(
      withAutoApprove(
        {
          repo: "org/repo",
          extensions: { issue_number: 42 },
        },
        true,
      ),
    ).toMatchObject({
      repo: "org/repo",
      extensions: {
        issue_number: 42,
        auto_approve: true,
      },
    });
  });
});
