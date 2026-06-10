import { describe, expect, it } from "vitest";

import {
  parseInitialState,
  parsePipelineVersion,
  withAutoApprove,
} from "./run-trigger-options";

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

describe("parseInitialState", () => {
  it("returns an empty object for blank/whitespace input", () => {
    expect(parseInitialState("")).toEqual({ ok: true, value: {} });
    expect(parseInitialState("   \n")).toEqual({ ok: true, value: {} });
  });

  it("parses a JSON object", () => {
    expect(parseInitialState('{"repo": "org/repo"}')).toEqual({
      ok: true,
      value: { repo: "org/repo" },
    });
  });

  it("rejects JSON that is not an object", () => {
    for (const bad of ["[1,2]", '"str"', "42", "null"]) {
      const result = parseInitialState(bad);
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.error).toMatch(/must be a JSON object/);
      }
    }
  });

  it("rejects malformed JSON with the parser message", () => {
    const result = parseInitialState("{nope");
    expect(result.ok).toBe(false);
  });
});

describe("parsePipelineVersion", () => {
  it("returns undefined for the sentinel value", () => {
    expect(parsePipelineVersion("current", "current")).toBeUndefined();
    expect(parsePipelineVersion("latest", "latest")).toBeUndefined();
  });

  it("parses a numeric version string", () => {
    expect(parsePipelineVersion("3", "current")).toBe(3);
  });

  it("returns undefined for a non-numeric value", () => {
    expect(parsePipelineVersion("garbage", "current")).toBeUndefined();
  });
});
