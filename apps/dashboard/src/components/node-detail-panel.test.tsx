import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "@/lib/api/client";
import type { NodeExecutionLog, StateSnapshot } from "@/lib/api/types";

// Hoisted mocks so the vi.mock factory can close over them.
const mockUseRunNodeLog = vi.fn();
const mockUseRunStateHistory = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mockUseAgentsList = vi.fn((): { data: { items: any[] } } => ({ data: { items: [] } }));
const mockUseRunNodeExplain = vi.fn(() => ({ data: null, isLoading: false, isError: false }));

vi.mock("@/hooks/api", () => ({
  useRunNodeLog: (...args: unknown[]) => mockUseRunNodeLog(...args),
  useRunStateHistory: (...args: unknown[]) => mockUseRunStateHistory(...args),
  useAgentsList: () => mockUseAgentsList(),
  useRunNodeExplain: () => mockUseRunNodeExplain(),
}));

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

const mockSetPrefill = vi.fn();
vi.mock("@/components/assistant/assistant-prefill", () => ({
  useAssistantPrefill: () => ({
    setPrefill: mockSetPrefill,
    consumePrefill: () => null,
  }),
}));

import { NodeDetailPanel, inputStateForNode } from "./node-detail-panel";

const _snap = (node_id: string, state: Record<string, unknown>): StateSnapshot =>
  ({ id: node_id, run_id: "r1", node_id, timestamp: "t", state }) as unknown as StateSnapshot;

describe("inputStateForNode (#749)", () => {
  const order = ["mockup", "mockup-write", "coder", "coder-write", "tester"];

  it("returns the most recent upstream snapshot for a node with no snapshot", () => {
    // Sparse cortex run: only *-write nodes recorded snapshots. Clicking `coder`
    // (no snapshot) should seed from the latest upstream snapshot (mockup-write).
    const snaps = [_snap("mockup-write", { extensions: { issue: 1 } })];
    expect(inputStateForNode(snaps, order, "coder")).toEqual({ extensions: { issue: 1 } });
  });

  it("picks the closest upstream snapshot when several precede the node", () => {
    const snaps = [
      _snap("mockup-write", { step: "mockup" }),
      _snap("coder-write", { step: "coder" }),
    ];
    expect(inputStateForNode(snaps, order, "tester")).toEqual({ step: "coder" });
  });

  it("returns null when nothing ran upstream", () => {
    expect(inputStateForNode([_snap("coder-write", { x: 1 })], order, "mockup")).toBeNull();
    expect(inputStateForNode([], order, "coder")).toBeNull();
  });
});

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

describe("NodeDetailPanel — node that didn't run", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
  });

  it("shows a friendly message (not an error) on 404 — the node never ran", () => {
    mockUseRunNodeLog.mockReturnValue({
      data: null,
      isPending: false,
      isError: true,
      error: new ApiError(404, "Node not found"),
    });
    mockUseRunStateHistory.mockReturnValue({ data: [] });
    render(<NodeDetailPanel runId="r1" nodeId="coder" onOpenChange={() => {}} />);

    expect(screen.getByText(/didn.?t run in this run/i)).toBeInTheDocument();
    expect(screen.queryByText(/failed to load/i)).toBeNull();
  });

  it("still shows a hard error for non-404 failures", () => {
    mockUseRunNodeLog.mockReturnValue({
      data: null,
      isPending: false,
      isError: true,
      error: new ApiError(500, "boom"),
    });
    mockUseRunStateHistory.mockReturnValue({ data: [] });
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
  });
});

describe("NodeDetailPanel — cortex prompt + agent identity (#724)", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
    mockUseAgentsList.mockReset();
    mockUseAgentsList.mockReturnValue({ data: { items: [] } });
  });

  it("shows the recorded prompt for a cortex python-func node", async () => {
    const user = userEvent.setup();
    mockUseRunNodeLog.mockReturnValue({
      data: makeLog({
        runtime_id: "python-func",
        prompt_xml: "",
        output_json: {
          state_delta: {
            extensions: {
              __audit: {
                system_prompt: "You are the Mockup agent.",
                user_prompt: "Issue #162: add IR paths",
              },
            },
          },
        },
      }),
      isPending: false,
      isError: false,
      error: null,
    });
    mockUseRunStateHistory.mockReturnValue({ data: [] });
    render(<NodeDetailPanel runId="r1" nodeId="mockup" onOpenChange={() => {}} />);

    // Prompt tab IS shown (cortex recorded a prompt) even though it's python-func.
    const promptTab = screen.getByRole("tab", { name: /prompt/i });
    await user.click(promptTab);
    expect(screen.getByText(/You are the Mockup agent/)).toBeInTheDocument();
    expect(screen.getByText(/Issue #162/)).toBeInTheDocument();
  });

  it("shows the agent name + Cortex tag", () => {
    mockUseRunNodeLog.mockReturnValue({
      data: makeLog({ runtime_id: "python-func", agent_id: "a-1", prompt_xml: "" }),
      isPending: false,
      isError: false,
      error: null,
    });
    mockUseRunStateHistory.mockReturnValue({ data: [] });
    mockUseAgentsList.mockReturnValue({
      data: {
        items: [
          {
            id: "a-1",
            name: "Cortex Phase 1 — Mockup",
            role: "prompt_builder",
            runtime_config: { callable_path: "cortex.nodes.mockup:run" },
          },
        ],
      },
    });
    render(<NodeDetailPanel runId="r1" nodeId="mockup" onOpenChange={() => {}} />);
    expect(screen.getByText("Cortex Phase 1 — Mockup")).toBeInTheDocument();
    expect(screen.getByText("Cortex")).toBeInTheDocument();
    expect(screen.getByText("prompt_builder")).toBeInTheDocument();
  });
});

describe("NodeDetailPanel — Open in agent tester (#724 part 4)", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
    mockUseAgentsList.mockReset();
    mockPush.mockReset();
    mockSetPrefill.mockReset();
  });

  it("stashes the input state and navigates to the agent's edit page with ?tab=test", async () => {
    const user = userEvent.setup();
    const beforeState = {
      run_id: "r1",
      repo: "lagowski/dap",
      extensions: { issue_number: 42 },
    };
    const snapshots: StateSnapshot[] = [
      {
        id: "s0",
        run_id: "r1",
        node_id: "prev",
        timestamp: "2026-01-01T00:00:00Z",
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        state: beforeState as any,
      },
      {
        id: "s1",
        run_id: "r1",
        node_id: "n1",
        timestamp: "2026-01-01T00:00:01Z",
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        state: { ...beforeState, extra: 1 } as any,
      },
    ];
    mockUseRunNodeLog.mockReturnValue({
      data: makeLog({ agent_id: "a-1", runtime_id: "python-func", prompt_xml: "" }),
      isPending: false,
      isError: false,
      error: null,
    });
    mockUseRunStateHistory.mockReturnValue({ data: snapshots });
    mockUseAgentsList.mockReturnValue({
      data: {
        items: [
          {
            id: "a-1",
            name: "Coder",
            role: "developer",
            runtime_config: { callable_path: "cortex.nodes.coder:run" },
          },
        ],
      },
    });
    const onOpenChange = vi.fn();
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={onOpenChange} />);

    await user.click(screen.getByRole("button", { name: /open in agent tester/i }));

    expect(mockSetPrefill).toHaveBeenCalledTimes(1);
    const payload = mockSetPrefill.mock.calls[0][0];
    expect(payload.target).toBe("agent-test");
    expect(payload.values.agent_id).toBe("a-1");
    // ``input_state`` is the *before* snapshot, not the *after*.
    expect(payload.values.input_state).toMatchObject({
      run_id: "r1",
      extensions: { issue_number: 42 },
    });
    expect(payload.values.input_state.extra).toBeUndefined();
    expect(mockPush).toHaveBeenCalledWith("/agents/a-1/edit?tab=test");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("hides the 'Open in agent tester' button when the agent hasn't resolved", () => {
    mockUseRunNodeLog.mockReturnValue({
      data: makeLog({ agent_id: "unknown-agent" }),
      isPending: false,
      isError: false,
      error: null,
    });
    mockUseRunStateHistory.mockReturnValue({ data: [] });
    mockUseAgentsList.mockReturnValue({ data: { items: [] } });
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    expect(screen.queryByRole("button", { name: /open in agent tester/i })).toBeNull();
  });
});
