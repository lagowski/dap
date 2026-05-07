/**
 * Edge color and label derivation from EdgeCondition (#227).
 *
 * Classifies a structured EdgeCondition into a semantic category so the
 * run graph can colour conditional edges green (pass) or red (fail/retry).
 */
import type { EdgeCondition } from "@/lib/api/types";

export type EdgeCategory = "pass" | "fail" | "retry" | "max_attempts" | "neutral";

const PASS_STRINGS = ["approved", "pass", "success", "clean", "true"];
const FAIL_STRINGS = ["rejected", "fail", "needs_work", "false"];
const RETRY_FIELDS = ["retry", "attempt"];

/** Recursively classify a condition into a semantic category. */
export function classifyCondition(condition: EdgeCondition): EdgeCategory {
  if (condition.type === "comparison") {
    const { field, operator, value } = condition;

    // Boolean literal checks
    if (value === true) return operator === "==" ? "pass" : "fail";
    if (value === false) return operator === "==" ? "fail" : "pass";

    // Retry / attempt count patterns  e.g. retry_count < 2
    const fieldLower = field.toLowerCase();
    if (RETRY_FIELDS.some((r) => fieldLower.includes(r))) {
      if (operator === "<" || operator === "<=") return "retry";
      if (operator === ">=" || operator === ">") return "max_attempts";
    }

    // String value checks
    if (typeof value === "string") {
      const v = value.toLowerCase();
      if (PASS_STRINGS.some((s) => v.includes(s))) return "pass";
      if (FAIL_STRINGS.some((s) => v.includes(s))) return "fail";
    }

    return "neutral";
  }

  // Logical: classify children and return the first non-neutral result
  for (const child of condition.children) {
    const cat = classifyCondition(child);
    if (cat !== "neutral") return cat;
  }
  return "neutral";
}

export const EDGE_COLORS: Record<EdgeCategory, string> = {
  pass: "#22c55e", // green-500
  fail: "#ef4444", // red-500
  retry: "#ef4444", // red-500
  max_attempts: "#f59e0b", // amber-500
  neutral: "#3b82f6", // blue-500 (existing conditional default)
};

export const EDGE_LABELS: Record<EdgeCategory, string> = {
  pass: "✓ approved",
  fail: "✗ rejected",
  retry: "↺ retry",
  max_attempts: "⚠ max attempts",
  neutral: "",
};

/** Return stroke colour for a conditional edge. */
export function getEdgeColor(condition: EdgeCondition): string {
  return EDGE_COLORS[classifyCondition(condition)];
}

/** Return a short centre label for a conditional edge, or empty string. */
export function getEdgeLabel(condition: EdgeCondition): string {
  return EDGE_LABELS[classifyCondition(condition)];
}
