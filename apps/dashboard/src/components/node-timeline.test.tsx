import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { NodeStatus } from "@/lib/api/types";

// Mock the API hook — we don't need real state history for unit tests.
vi.mock("@/hooks/api", () => ({
  useRunStateHistory: () => ({ data: null }),
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
  it("renders nothing when nodeStatuses is empty", () => {
    const { container } = render(
      <NodeTimeline nodeStatuses={{}} currentNode={null} runId="r1" />,
    );
    expect(container).toBeEmptyDOMElement();
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
});
