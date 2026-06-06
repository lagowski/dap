import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { NodeStatus } from "@/lib/api/types";

// Default mock — no logs (simulates a run that hasn't started yet, or DB drop).
// Use vi.fn() so individual tests can override with mockReturnValueOnce.
// Hoisted so vi.mock factory can close over it.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mockUseRunNodeLogs = vi.fn(() => ({ data: null as any }));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mockUseAgentsList = vi.fn(() => ({ data: null as any }));
vi.mock("@/hooks/api", () => ({
  useRunNodeLogs: () => mockUseRunNodeLogs(),
  useAgentsList: () => mockUseAgentsList(),
}));

// Mock lucide-react icons to simple spans for testing.
vi.mock("lucide-react", () => ({
  CheckCircle2: (props: Record<string, unknown>) => <span data-testid="icon-check" {...props} />,
  Circle: (props: Record<string, unknown>) => <span data-testid="icon-circle" {...props} />,
  Loader2: (props: Record<string, unknown>) => <span data-testid="icon-loader" {...props} />,
  XCircle: (props: Record<string, unknown>) => <span data-testid="icon-x" {...props} />,
  MinusCircle: (props: Record<string, unknown>) => <span data-testid="icon-minus" {...props} />,
}));

import { NodeTimeline } from "./node-timeline";

describe("NodeTimeline", () => {
  // Reset the per-render queues to a no-data default before each test so the
  // ``mockReturnValueOnce`` overrides are order-independent (the component now
  // calls two hooks per render — useRunNodeLogs + useAgentsList).
  beforeEach(() => {
    mockUseRunNodeLogs.mockReset().mockReturnValue({ data: null });
    mockUseAgentsList.mockReset().mockReturnValue({ data: null });
  });

  it("renders nothing when nodeStatuses is empty", () => {
    const { container } = render(
      <NodeTimeline nodeStatuses={{}} currentNode={null} runId="r1" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the agent name and runtime for each node (#707)", () => {
    mockUseRunNodeLogs.mockReturnValueOnce({
      data: [
        {
          node_id: "mockup",
          agent_id: "a-1",
          runtime_id: "claude-code",
          started_at: "2026-01-01T00:00:00Z",
          ended_at: "2026-01-01T00:00:01Z",
          duration_ms: 1000,
        },
        {
          node_id: "finalize-write",
          agent_id: "a-2",
          runtime_id: "python-func",
          started_at: "2026-01-01T00:00:02Z",
          ended_at: "2026-01-01T00:00:03Z",
          duration_ms: 1000,
        },
      ],
    });
    mockUseAgentsList.mockReturnValueOnce({
      data: {
        items: [
          { id: "a-1", name: "TaskSelector" },
          { id: "a-2", name: "FinalizeWriter" },
        ],
      },
    });
    const statuses: Record<string, NodeStatus> = {
      mockup: "success",
      "finalize-write": "success",
    };
    render(
      <NodeTimeline nodeStatuses={statuses} currentNode={null} runId="r1" />,
    );
    // Agent + runtime render as one secondary "(name · runtime)" label.
    expect(screen.getByText(/TaskSelector · claude-code/)).toBeInTheDocument();
    expect(screen.getByText(/FinalizeWriter · python-func/)).toBeInTheDocument();
  });

  it("falls back to a short agent id when the agent is unknown (#707)", () => {
    mockUseRunNodeLogs.mockReturnValueOnce({
      data: [
        {
          node_id: "mockup",
          agent_id: "abcdef12-3456-7890-abcd-ef1234567890",
          runtime_id: "bash",
          started_at: "2026-01-01T00:00:00Z",
          ended_at: "2026-01-01T00:00:01Z",
          duration_ms: 1000,
        },
      ],
    });
    mockUseAgentsList.mockReturnValueOnce({ data: { items: [] } });
    render(
      <NodeTimeline
        nodeStatuses={{ mockup: "success" }}
        currentNode={null}
        runId="r1"
      />,
    );
    // Short prefix of the agent id, not the full uuid.
    expect(screen.getByText(/abcdef12/)).toBeInTheDocument();
  });

  it("renders all-pending nodes", () => {
    const statuses: Record<string, NodeStatus> = {
      mockup: "pending",
      specify: "pending",
      coder: "pending",
    };
    render(
      <NodeTimeline nodeStatuses={statuses} currentNode={null} runId="r1" />,
    );
    expect(screen.getByText("mockup")).toBeInTheDocument();
    expect(screen.getByText("specify")).toBeInTheDocument();
    expect(screen.getByText("coder")).toBeInTheDocument();
    expect(screen.getAllByTestId("icon-circle")).toHaveLength(3);
  });

  it("renders mixed statuses with correct icons", () => {
    const statuses: Record<string, NodeStatus> = {
      mockup: "success",
      specify: "success",
      finalize: "running",
      coder: "pending",
    };
    render(
      <NodeTimeline
        nodeStatuses={statuses}
        currentNode="finalize"
        runId="r1"
      />,
    );
    expect(screen.getAllByTestId("icon-check")).toHaveLength(2);
    expect(screen.getAllByTestId("icon-loader")).toHaveLength(1);
    expect(screen.getAllByTestId("icon-circle")).toHaveLength(1);
  });

  it("highlights current node with font-semibold", () => {
    const statuses: Record<string, NodeStatus> = {
      mockup: "success",
      finalize: "running",
    };
    render(
      <NodeTimeline
        nodeStatuses={statuses}
        currentNode="finalize"
        runId="r1"
      />,
    );
    const finalize = screen.getByText("finalize");
    expect(finalize.className).toContain("font-semibold");
    const mockup = screen.getByText("mockup");
    expect(mockup.className).not.toContain("font-semibold");
  });

  it("renders all-complete nodes", () => {
    const statuses: Record<string, NodeStatus> = {
      mockup: "success",
      specify: "success",
      coder: "success",
    };
    render(
      <NodeTimeline nodeStatuses={statuses} currentNode={null} runId="r1" />,
    );
    expect(screen.getAllByTestId("icon-check")).toHaveLength(3);
  });

  it("renders failed node with error icon", () => {
    const statuses: Record<string, NodeStatus> = {
      mockup: "success",
      specify: "failed",
      coder: "skipped",
    };
    render(
      <NodeTimeline nodeStatuses={statuses} currentNode={null} runId="r1" />,
    );
    expect(screen.getAllByTestId("icon-check")).toHaveLength(1);
    expect(screen.getAllByTestId("icon-x")).toHaveLength(1);
    expect(screen.getAllByTestId("icon-minus")).toHaveLength(1);
  });

  it("shows accurate duration from execution log duration_ms", () => {
    mockUseRunNodeLogs.mockReturnValueOnce({
      data: [
        {
          node_id: "finalize",
          started_at: "2026-05-27T13:48:13Z",
          ended_at: "2026-05-27T13:48:23Z",
          duration_ms: 10_000,
          status: "success",
        },
        {
          node_id: "pr_creator",
          started_at: "2026-05-27T13:48:23Z",
          ended_at: "2026-05-27T13:48:45Z",
          duration_ms: 22_000,
          status: "success",
        },
      ],
    });

    const statuses: Record<string, NodeStatus> = {
      finalize: "success",
      pr_creator: "success",
    };
    render(
      <NodeTimeline nodeStatuses={statuses} currentNode={null} runId="r1" />,
    );
    // finalize: 10.0s, pr_creator: 22.0s (formatDuration uses toFixed(1))
    expect(screen.getByText("10.0s")).toBeInTheDocument();
    expect(screen.getByText("22.0s")).toBeInTheDocument();
  });

  it("does not show timing for nodes missing an execution log (DB-drop scenario)", () => {
    // Simulate: finalize log committed, pr_creator log never committed (DB drop).
    // The critical bug: without this fix the old code used Date.now() for the last
    // snapshot, producing a 20-minute finalize duration.
    mockUseRunNodeLogs.mockReturnValueOnce({
      data: [
        {
          node_id: "finalize",
          started_at: "2026-05-27T13:48:13Z",
          ended_at: "2026-05-27T13:48:23Z",
          duration_ms: 10_000,
          status: "success",
        },
        // pr_creator log missing — DB dropped before it could commit
      ],
    });

    const statuses: Record<string, NodeStatus> = {
      finalize: "success",
      pr_creator: "success", // ran but no log written
    };
    render(
      <NodeTimeline nodeStatuses={statuses} currentNode={null} runId="r1" />,
    );
    expect(screen.getByText("finalize")).toBeInTheDocument();
    expect(screen.getByText("pr_creator")).toBeInTheDocument();
    // finalize shows its real 10.0s duration from the log
    expect(screen.getByText("10.0s")).toBeInTheDocument();
    // No inflated time displayed — certainly no "20m"
    expect(screen.queryByText(/20m/)).not.toBeInTheDocument();
    // pr_creator has no log — no timing rendered for it
    // (Absence of a second duration string — only "10s" should appear)
  });
});
