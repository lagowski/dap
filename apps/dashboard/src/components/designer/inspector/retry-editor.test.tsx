import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { RetryPolicy } from "@/lib/api/types";

import { RetryEditor, defaultRetryPolicy } from "./retry-editor";

const NODE_IDS = ["coder", "tester", "escalate"];

const POLICY: RetryPolicy = {
  max_retries: 3,
  retry_to: "coder",
  on_failure: "escalate",
  feedback_into: "test_feedback",
  short_circuit_on: null,
};

describe("defaultRetryPolicy", () => {
  it("retries the first other node and ends on failure", () => {
    expect(defaultRetryPolicy("tester", NODE_IDS)).toEqual({
      max_retries: 3,
      retry_to: "coder",
      on_failure: "__end__",
      feedback_into: null,
      short_circuit_on: null,
    });
  });

  it("falls back to the node itself when it is alone", () => {
    expect(defaultRetryPolicy("solo", ["solo"]).retry_to).toBe("solo");
  });
});

describe("RetryEditor", () => {
  it("offers to add a policy when the node has none", async () => {
    const onChange = vi.fn();
    render(<RetryEditor nodeId="tester" nodeIds={NODE_IDS} retry={null} onChange={onChange} />);

    expect(screen.getByText(/No retry policy/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add retry/ }));
    expect(onChange).toHaveBeenCalledWith(defaultRetryPolicy("tester", NODE_IDS));
  });

  it("shows the current policy", () => {
    render(<RetryEditor nodeId="tester" nodeIds={NODE_IDS} retry={POLICY} onChange={vi.fn()} />);

    expect(screen.getByLabelText("Max retries")).toHaveValue(3);
    expect(screen.getByLabelText("Retry to")).toHaveValue("coder");
    expect(screen.getByLabelText("On failure")).toHaveValue("escalate");
    expect(screen.getByLabelText("Feedback into")).toHaveValue("test_feedback");
  });

  it("lists __end__ as an on_failure target but not as a retry target", () => {
    render(<RetryEditor nodeId="tester" nodeIds={NODE_IDS} retry={POLICY} onChange={vi.fn()} />);

    const retryTo = screen.getByLabelText("Retry to");
    const onFailure = screen.getByLabelText("On failure");
    expect(Array.from(retryTo.querySelectorAll("option")).map((o) => o.value)).toEqual(NODE_IDS);
    expect(Array.from(onFailure.querySelectorAll("option")).map((o) => o.value)).toEqual([
      ...NODE_IDS,
      "__end__",
    ]);
  });

  it("edits max_retries, targets and feedback key", () => {
    const onChange = vi.fn();
    render(<RetryEditor nodeId="tester" nodeIds={NODE_IDS} retry={POLICY} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Max retries"), { target: { value: "5" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...POLICY, max_retries: 5 });

    fireEvent.change(screen.getByLabelText("Retry to"), { target: { value: "tester" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...POLICY, retry_to: "tester" });

    fireEvent.change(screen.getByLabelText("On failure"), { target: { value: "__end__" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...POLICY, on_failure: "__end__" });

    fireEvent.change(screen.getByLabelText("Feedback into"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...POLICY, feedback_into: null });
  });

  it("adds a short-circuit condition", async () => {
    const onChange = vi.fn();
    render(<RetryEditor nodeId="tester" nodeIds={NODE_IDS} retry={POLICY} onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: /Comparison/ }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...POLICY,
      short_circuit_on: { type: "comparison", field: "tests_passed", operator: "==", value: true },
    });
  });

  it("removes the policy", async () => {
    const onChange = vi.fn();
    render(<RetryEditor nodeId="tester" nodeIds={NODE_IDS} retry={POLICY} onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: /Remove retry/ }));
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
