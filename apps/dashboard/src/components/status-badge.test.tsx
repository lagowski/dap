import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RunStatusBadge } from "./status-badge";

describe("RunStatusBadge", () => {
  it("shows static badge for terminal statuses", () => {
    const { rerender } = render(<RunStatusBadge status="success" />);
    expect(screen.getByText("success")).toBeInTheDocument();

    rerender(<RunStatusBadge status="failed" />);
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("shows spinner and node name when running with currentNode", () => {
    render(<RunStatusBadge status="running" currentNode="finalize" />);
    expect(screen.getByText("finalize")).toBeInTheDocument();
    // Loader2 renders as an svg with animate-spin
    const svg = document.querySelector(".animate-spin");
    expect(svg).toBeInTheDocument();
  });

  it("falls back to static badge when running without currentNode", () => {
    render(<RunStatusBadge status="running" />);
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("shows pulsing badge and approve button when paused at gate", async () => {
    const onApprove = vi.fn();
    const user = userEvent.setup();
    render(
      <RunStatusBadge
        status="paused"
        pausedAtNode="gate-phase1"
        onApprove={onApprove}
      />,
    );

    expect(screen.getByText("gate-phase1")).toBeInTheDocument();
    expect(screen.getByText("— waiting")).toBeInTheDocument();
    // Pulsing animation class
    const badge = screen.getByText("gate-phase1").closest(".animate-pulse");
    expect(badge).toBeInTheDocument();

    const btn = screen.getByRole("button", { name: /approve/i });
    expect(btn).toBeInTheDocument();
    await user.click(btn);
    expect(onApprove).toHaveBeenCalledOnce();
  });

  it("does not render approve button when no onApprove handler", () => {
    render(<RunStatusBadge status="paused" pausedAtNode="gate-phase1" />);
    expect(screen.getByText("gate-phase1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });
});
