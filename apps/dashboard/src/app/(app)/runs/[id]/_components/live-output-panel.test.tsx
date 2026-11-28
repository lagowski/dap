/**
 * Tests for ``LiveOutputPanel`` (#662 Phase 3c).
 *
 * The panel consumes ``useRunEvents`` for streamed stdout. We mock that
 * hook so each test drives a deterministic stream state and asserts the
 * panel's render contract independently of EventSource plumbing (which is
 * covered by ``use-run-events.test.tsx``).
 *
 * Pinned contracts:
 *   - renders streamed ``node_log`` content as joined monospace text.
 *   - shows the current node id in the header.
 *   - collapses / expands the body via the header toggle.
 *   - waiting state while streaming with no output yet.
 *   - finished/idle empty state when not streaming and no output.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { UseRunEventsResult } from "@/hooks/use-run-events";

const mockUseRunEvents = vi.fn<() => UseRunEventsResult>();
vi.mock("@/hooks/use-run-events", () => ({
  RUN_LOG_LINE_CAP: 1000,
  useRunEvents: () => mockUseRunEvents(),
}));

// jsdom doesn't implement scrollTo / scrollHeight meaningfully; stub so the
// auto-scroll effect doesn't throw.
beforeEach(() => {
  Element.prototype.scrollTo = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    value: 1000,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    value: 200,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

import { LiveOutputPanel } from "./live-output-panel";

function setStream(partial: Partial<UseRunEventsResult>) {
  mockUseRunEvents.mockReturnValue({
    logLines: [],
    isStreaming: false,
    currentNode: null,
    ...partial,
  });
}

describe("LiveOutputPanel", () => {
  it("renders streamed node_log content as text", () => {
    setStream({
      isStreaming: true,
      currentNode: "coder",
      logLines: [
        { node_id: "coder", seq: 1, content: "compiling…\n", stream: "stdout" },
        { node_id: "coder", seq: 2, content: "done\n", stream: "stdout" },
      ],
    });
    render(<LiveOutputPanel runId="run-1" enabled />);
    const body = screen.getByText(/compiling…/);
    expect(body.textContent).toContain("done");
  });

  it("shows the current node id in the header", () => {
    setStream({ isStreaming: true, currentNode: "cortex", logLines: [] });
    render(<LiveOutputPanel runId="run-1" enabled />);
    expect(screen.getByText(/cortex/)).toBeInTheDocument();
  });

  it("shows a waiting state while streaming with no output yet", () => {
    setStream({ isStreaming: true, currentNode: "coder", logLines: [] });
    render(<LiveOutputPanel runId="run-1" enabled />);
    expect(screen.getByText(/waiting for output/i)).toBeInTheDocument();
  });

  it("collapses and expands the body via the header toggle", async () => {
    setStream({
      isStreaming: true,
      currentNode: "coder",
      logLines: [
        { node_id: "coder", seq: 1, content: "hello-line\n", stream: "stdout" },
      ],
    });
    render(<LiveOutputPanel runId="run-1" enabled />);

    // Expanded by default — content visible.
    expect(screen.getByText(/hello-line/)).toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: /live output/i });
    await userEvent.click(toggle); // collapse
    expect(screen.queryByText(/hello-line/)).not.toBeInTheDocument();

    await userEvent.click(toggle); // expand
    expect(screen.getByText(/hello-line/)).toBeInTheDocument();
  });

  it("shows an idle empty state when not streaming and no output", () => {
    setStream({ isStreaming: false, currentNode: null, logLines: [] });
    render(<LiveOutputPanel runId="run-1" enabled />);
    expect(screen.getByText(/no streamed output/i)).toBeInTheDocument();
  });
});
