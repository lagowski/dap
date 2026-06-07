import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ManagedDryRunWarning } from "./managed-dry-run-warning";

describe("ManagedDryRunWarning (#739 slice 3)", () => {
  it("warns that the dry-run executes the real callable with side effects", () => {
    render(<ManagedDryRunWarning checked={false} onCheckedChange={() => {}} />);
    expect(screen.getByText(/isn.?t a safe sandbox/i)).toBeInTheDocument();
    expect(screen.getByText(/write to github/i)).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).not.toBeChecked();
  });

  it("reports the acknowledgement toggle", async () => {
    const onCheckedChange = vi.fn();
    const user = userEvent.setup();
    render(<ManagedDryRunWarning checked={false} onCheckedChange={onCheckedChange} />);
    await user.click(screen.getByRole("checkbox"));
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it("reflects the checked state", () => {
    render(<ManagedDryRunWarning checked onCheckedChange={() => {}} />);
    expect(screen.getByRole("checkbox")).toBeChecked();
  });
});
