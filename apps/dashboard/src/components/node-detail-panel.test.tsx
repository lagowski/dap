import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { NodeExecutionLog, StateSnapshot } from "@/lib/api/types";

// Hoisted mocks so the vi.mock factory can close over them.
const mockUseRunNodeLog = vi.fn();
const mockUseRunStateHistory = vi.fn();

vi.mock("@/hooks/api", () => ({
  useRunNodeLog: (...args: unknown[]) => mockUseRunNodeLog(...args),
  useRunStateHistory: (...args: unknown[]) => mockUseRunStateHistory(...args),
}));

import { NodeDetailPanel } from "./node-detail-panel";

function makeLog(overrides: Partial<NodeExecutionLog> = {}): NodeExecutionLog {
  return {
    id: "log-1",
    run_id: "r1",
    node_id: "n1",
    agent_id: "agent-1",
    runtime_id: "claude-code",
    started_at: "2026-01-01T00:00:00Z",
    ended_at: "2026-01-01T00:00:03Z",
    prompt_xml: "<agent_prompt>do the thing</agent_prompt>",
    stdout: "",
    stderr: "",
    output_json: null,
    tokens_used: 0,
    cost_usd: 0,
    duration_ms: 3200,
    status: "success",
    error_message: null,
    ...overrides,
  };
}

function mockLog(log: NodeExecutionLog | null, snapshots: StateSnapshot[] = []) {
  mockUseRunNodeLog.mockReturnValue({
    data: log,
    isPending: false,
    isError: false,
    error: null,
  });
  mockUseRunStateHistory.mockReturnValue({ data: snapshots });
}

describe("NodeDetailPanel — Prompt tab", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
  });

  it("hides the Prompt tab for python-func nodes (no prompt by design)", () => {
    mockLog(makeLog({ runtime_id: "python-func", prompt_xml: "" }));
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    expect(screen.queryByRole("tab", { name: /prompt/i })).toBeNull();
    // Std output is always present, so the tab strip still renders.
    expect(screen.getByRole("tab", { name: /std output/i })).toBeInTheDocument();
  });

  it("shows the Prompt tab with the rendered prompt for LLM nodes", async () => {
    const user = userEvent.setup();
    mockLog(makeLog({ runtime_id: "claude-code", prompt_xml: "<agent_prompt>hello world</agent_prompt>" }));
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    const promptTab = screen.getByRole("tab", { name: /prompt/i });
    expect(promptTab).toBeInTheDocument();
    await user.click(promptTab);
    expect(screen.getByText(/hello world/)).toBeInTheDocument();
  });

  it("shows a placeholder when an LLM node recorded no prompt", async () => {
    const user = userEvent.setup();
    mockLog(makeLog({ runtime_id: "claude-code", prompt_xml: "" }));
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    const promptTab = screen.getByRole("tab", { name: /prompt/i });
    await user.click(promptTab);
    expect(screen.getByText(/no prompt recorded/i)).toBeInTheDocument();
  });
});

describe("NodeDetailPanel — State diff empty states", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
  });

  it("distinguishes 'no snapshot' from 'nothing changed'", async () => {
    const user = userEvent.setup();
    mockLog(makeLog({ runtime_id: "python-func", prompt_xml: "" }), []);
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    await user.click(screen.getByRole("tab", { name: /state diff/i }));
    expect(screen.getByText(/no state (snapshot|history)/i)).toBeInTheDocument();
  });
});
