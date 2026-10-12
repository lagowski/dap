/**
 * ConditionBuilder — ``not`` support in the logical editor (#930).
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { EdgeCondition, LogicalCondition } from "@/lib/api/types";
import { ConditionBuilder, withLogicalType } from "./condition-builder";

const cmp = (field: string): EdgeCondition => ({
  type: "comparison",
  field,
  operator: "==",
  value: true,
});

describe("withLogicalType", () => {
  it("keeps only the first child when switching to not", () => {
    const group: LogicalCondition = { type: "and", children: [cmp("a"), cmp("b")] };
    expect(withLogicalType(group, "not")).toEqual({ type: "not", children: [cmp("a")] });
  });

  it("gives not a default child when the group was empty", () => {
    const next = withLogicalType({ type: "or", children: [] }, "not");
    expect(next.type).toBe("not");
    expect(next.children).toHaveLength(1);
  });

  it("keeps every child when switching between and/or", () => {
    const group: LogicalCondition = { type: "and", children: [cmp("a"), cmp("b")] };
    expect(withLogicalType(group, "or")).toEqual({ type: "or", children: [cmp("a"), cmp("b")] });
  });
});

describe("ConditionBuilder", () => {
  it("offers NOT in the logical operator select", () => {
    render(
      <ConditionBuilder
        condition={{ type: "and", children: [cmp("tests_passed")] }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("option", { name: "NOT" })).toBeInTheDocument();
  });

  it("switching to NOT emits a single-child not", () => {
    const onChange = vi.fn();
    render(
      <ConditionBuilder
        condition={{ type: "and", children: [cmp("tests_passed"), cmp("attempt")] }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByDisplayValue("AND"), { target: { value: "not" } });
    expect(onChange).toHaveBeenCalledWith({ type: "not", children: [cmp("tests_passed")] });
  });

  it("a not group cannot gain or lose its child", () => {
    render(
      <ConditionBuilder
        condition={{ type: "not", children: [cmp("tests_passed")] }}
        onChange={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /add child/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /remove child/i })).toBeNull();
  });

  it("an and group can add and remove children", () => {
    render(
      <ConditionBuilder
        condition={{ type: "and", children: [cmp("tests_passed")] }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /add child/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove child/i })).toBeInTheDocument();
  });

  it("offers a NOT starter when there is no condition", () => {
    const onChange = vi.fn();
    render(<ConditionBuilder condition={null} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^not$/i }));
    expect(onChange).toHaveBeenCalledWith({
      type: "not",
      children: [{ type: "comparison", field: "tests_passed", operator: "==", value: true }],
    });
  });
});
