import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ManagedAgentsFilterToggle } from "./managed-agents-filter-toggle";

describe("ManagedAgentsFilterToggle (#739 slice 2)", () => {
  it("renders nothing when there are no managed agents", () => {
    const { container } = render(
      <ManagedAgentsFilterToggle count={0} checked={false} onCheckedChange={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the count and pluralizes", () => {
    const { rerender } = render(
      <ManagedAgentsFilterToggle count={1} checked={false} onCheckedChange={() => {}} />,
    );
    expect(screen.getByText(/hide 1 managed \(cortex\) agent$/i)).toBeInTheDocument();
    rerender(
      <ManagedAgentsFilterToggle count={3} checked={false} onCheckedChange={() => {}} />,
    );
    expect(screen.getByText(/hide 3 managed \(cortex\) agents$/i)).toBeInTheDocument();
  });

  it("reflects and reports the checked state", async () => {
    const onCheckedChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ManagedAgentsFilterToggle count={2} checked={false} onCheckedChange={onCheckedChange} />,
    );
    const box = screen.getByRole("checkbox");
    expect(box).not.toBeChecked();
    await user.click(box);
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });
});
