import { describe, it, expect } from "vitest";
import type { EdgeCondition } from "@/lib/api/types";
import {
  classifyCondition,
  getEdgeColor,
  getEdgeLabel,
  EDGE_COLORS,
  EDGE_LABELS,
} from "./edge-color";

// Helpers
const cmp = (
  field: string,
  operator: "==" | "!=" | "<" | "<=" | ">" | ">=",
  value: string | number | boolean | null,
): EdgeCondition => ({ type: "comparison", field, operator, value });

const and = (...children: EdgeCondition[]): EdgeCondition => ({
  type: "and",
  children,
});

describe("classifyCondition", () => {
  it("classifies == true as pass", () => {
    expect(classifyCondition(cmp("review_approved", "==", true))).toBe("pass");
  });

  it("classifies != true as fail", () => {
    expect(classifyCondition(cmp("review_approved", "!=", true))).toBe("fail");
  });

  it("classifies == false as fail", () => {
    expect(classifyCondition(cmp("review_approved", "==", false))).toBe("fail");
  });

  it("classifies != false as pass", () => {
    expect(classifyCondition(cmp("review_approved", "!=", false))).toBe("pass");
  });

  it("classifies retry_count < N as retry", () => {
    expect(
      classifyCondition(cmp("extensions.pr_merger_retry_count", "<", 2)),
    ).toBe("retry");
  });

  it("classifies retry_count >= N as max_attempts", () => {
    expect(
      classifyCondition(cmp("extensions.pr_merger_retry_count", ">=", 2)),
    ).toBe("max_attempts");
  });

  it("classifies attempt < N as retry", () => {
    expect(classifyCondition(cmp("attempt", "<=", 3))).toBe("retry");
  });

  it("classifies exact string value 'approved' as pass", () => {
    expect(classifyCondition(cmp("status", "==", "approved"))).toBe("pass");
  });

  it("classifies exact string value 'rejected' as fail", () => {
    expect(classifyCondition(cmp("status", "==", "rejected"))).toBe("fail");
  });

  it("does not misclassify 'not_approved' as pass (substring false positive guard)", () => {
    expect(classifyCondition(cmp("status", "==", "not_approved"))).toBe(
      "neutral",
    );
  });

  it("does not misclassify 'fallback' as fail (substring false positive guard)", () => {
    expect(classifyCondition(cmp("status", "==", "fallback"))).toBe("neutral");
  });

  it("returns neutral for unrecognised comparison", () => {
    expect(classifyCondition(cmp("some_field", "==", 42))).toBe("neutral");
  });

  it("classifies logical AND by checking children", () => {
    const condition = and(
      cmp("some_field", "==", 99),
      cmp("review_approved", "==", true),
    );
    expect(classifyCondition(condition)).toBe("pass");
  });

  it("returns neutral for logical with all-neutral children", () => {
    const condition = and(cmp("x", "==", 1), cmp("y", "==", 2));
    expect(classifyCondition(condition)).toBe("neutral");
  });
});

describe("getEdgeColor", () => {
  it("returns green for pass conditions", () => {
    expect(getEdgeColor(cmp("approved", "==", true))).toBe(EDGE_COLORS.pass);
  });

  it("returns red for fail conditions", () => {
    expect(getEdgeColor(cmp("approved", "==", false))).toBe(EDGE_COLORS.fail);
  });

  it("returns red for retry conditions", () => {
    expect(
      getEdgeColor(cmp("retry_count", "<", 3)),
    ).toBe(EDGE_COLORS.retry);
  });

  it("returns amber for max_attempts conditions", () => {
    expect(
      getEdgeColor(cmp("retry_count", ">=", 3)),
    ).toBe(EDGE_COLORS.max_attempts);
  });
});

describe("getEdgeLabel", () => {
  it("returns approved label for pass", () => {
    expect(getEdgeLabel(cmp("review_approved", "==", true))).toBe(
      EDGE_LABELS.pass,
    );
  });

  it("returns retry label for retry", () => {
    expect(getEdgeLabel(cmp("retry_count", "<", 2))).toBe(EDGE_LABELS.retry);
  });

  it("returns max attempts label for max_attempts", () => {
    expect(getEdgeLabel(cmp("retry_count", ">=", 2))).toBe(
      EDGE_LABELS.max_attempts,
    );
  });

  it("returns empty string for neutral", () => {
    expect(getEdgeLabel(cmp("unknown_field", "==", 99))).toBe("");
  });
});

describe("classifyCondition — not (#930)", () => {
  const not = (child: EdgeCondition): EdgeCondition => ({
    type: "not",
    children: [child],
  });

  it("flips pass to fail", () => {
    expect(classifyCondition(not(cmp("review_approved", "==", true)))).toBe("fail");
  });

  it("flips fail to pass", () => {
    expect(classifyCondition(not(cmp("review_status", "==", "rejected")))).toBe("pass");
  });

  it("does not guess an inverse for retry or max-attempts", () => {
    expect(classifyCondition(not(cmp("retry_count", "<", 2)))).toBe("neutral");
    expect(classifyCondition(not(cmp("attempt", ">=", 3)))).toBe("neutral");
  });

  it("double negation restores the category", () => {
    expect(classifyCondition(not(not(cmp("review_approved", "==", true))))).toBe("pass");
  });

  it("not inside and is classified by its own inversion", () => {
    expect(
      classifyCondition(and(cmp("x", "==", 1), not(cmp("review_approved", "==", true)))),
    ).toBe("fail");
  });
});
