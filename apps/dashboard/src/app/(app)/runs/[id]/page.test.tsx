/**
 * Tests for the run-detail Live toggle (#662 Phase 2).
 *
 * The page itself unwraps ``params`` with React 19's ``use`` (a
 * Promise) and pulls in reactflow-heavy children, so a full page render
 * fights Suspense for no added coverage. Instead we exercise the
 * ``LiveToggle`` surface directly — the actual interactive contract the
 * issue requires — wired to the real ``useLiveUpdates`` hook so the
 * toggle ⇄ persistence path is covered end-to-end (matches the repo's
 * controlled-component test style, e.g. ``ControlledKV``).
 *
 * Pinned contracts:
 *   - Live on by default → no "paused" hint, no Refresh button.
 *   - Toggling off → "Live updates paused" + Refresh that calls onRefresh,
 *     and the choice persists to localStorage.
 *   - Toggling back on → hint + Refresh disappear.
 *   - Refresh button shows a spinner / is disabled while isFetching.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LiveToggle } from "./page";
import { useLiveUpdates } from "@/hooks/use-live-updates";

const STORAGE_KEY = "dap:run-live-updates";

// Stateful harness: drives LiveToggle off the real persisted hook so
// the toggle's effect on localStorage is part of the test, not mocked.
function ControlledToggle({
  onRefresh,
  isFetching = false,
}: {
  onRefresh: () => void;
  isFetching?: boolean;
}) {
  const [live, setLive] = useLiveUpdates();
  return (
    <LiveToggle
      live={live}
      onToggle={setLive}
      onRefresh={onRefresh}
      isFetching={isFetching}
    />
  );
}

describe("LiveToggle", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  it("defaults to Live on — no paused hint or Refresh button", () => {
    render(<ControlledToggle onRefresh={vi.fn()} />);
    expect(screen.getByRole("switch", { name: /toggle live updates/i }))
      .toHaveAttribute("aria-checked", "true");
    expect(screen.queryByText(/live updates paused/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refresh run/i })).not.toBeInTheDocument();
  });

  it("toggling off reveals the paused hint + Refresh and persists the choice", async () => {
    const onRefresh = vi.fn();
    render(<ControlledToggle onRefresh={onRefresh} />);
    const toggle = screen.getByRole("switch", { name: /toggle live updates/i });

    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/live updates paused/i)).toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("false");

    const refreshBtn = screen.getByRole("button", { name: /refresh run/i });
    await userEvent.click(refreshBtn);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("toggling back on hides the paused hint + Refresh again", async () => {
    render(<ControlledToggle onRefresh={vi.fn()} />);
    const toggle = screen.getByRole("switch", { name: /toggle live updates/i });

    await userEvent.click(toggle); // off
    expect(screen.getByText(/live updates paused/i)).toBeInTheDocument();

    await userEvent.click(toggle); // on
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByText(/live updates paused/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refresh run/i })).not.toBeInTheDocument();
  });

  it("disables the Refresh button while a fetch is in flight", () => {
    window.localStorage.setItem(STORAGE_KEY, "false");
    render(<ControlledToggle onRefresh={vi.fn()} isFetching />);
    expect(screen.getByRole("button", { name: /refresh run/i })).toBeDisabled();
  });
});
