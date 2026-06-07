import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "@/lib/api/client";
import type { Agent, NodeExecutionLog, StateSnapshot } from "@/lib/api/types";

// Hoisted mocks so the vi.mock factory can close over them.
const mockUseRunNodeLog = vi.fn();
const mockUseRunStateHistory = vi.fn();
const mockUseAgent = vi.fn();
const mockUseRunNodeExplain = vi.fn();

vi.mock("@/hooks/api", () => ({
  useRunNodeLog: (...args: unknown[]) => mockUseRunNodeLog(...args),
  useRunStateHistory: (...args: unknown[]) => mockUseRunStateHistory(...args),
  useAgent: (...args: unknown[]) => mockUseAgent(...args),
  useRunNodeExplain: (...args: unknown[]) => mockUseRunNodeExplain(...args),
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

import { NodeDetailPanel } from "./node-detail-panel";

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agent-1",
    name: "Coder",
    role: "developer",
    runtime_id: "python-func",
    runtime_config: {},
    prompt_template: "",
    input_schema: [],
    output_schema: [],
    constraints: {},
    budget_limit_usd: null,
    timeout_ms: null,
    version: 1,
    archived: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as Agent;
}

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

function mockLog(
  log: NodeExecutionLog | null,
  snapshots: StateSnapshot[] = [],
  agent: Agent | undefined = makeAgent(),
) {
  mockUseRunNodeLog.mockReturnValue({
    data: log,
    isPending: false,
    isError: false,
    error: null,
  });
  mockUseRunStateHistory.mockReturnValue({ data: snapshots });
  mockUseAgent.mockReturnValue({ data: agent });
  mockUseRunNodeExplain.mockReturnValue({ data: null, isLoading: false, isError: false });
}

describe("NodeDetailPanel — Prompt tab", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
    mockUseAgent.mockReset();
    mockUseRunNodeExplain.mockReset();
    mockPush.mockReset();
    mockSetPrefill.mockReset();
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
    mockUseAgent.mockReset();
    mockUseRunNodeExplain.mockReset();
    mockPush.mockReset();
    mockSetPrefill.mockReset();
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
    mockUseAgent.mockReset();
    mockUseRunNodeExplain.mockReset();
    mockPush.mockReset();
    mockSetPrefill.mockReset();
  });

  it("shows a friendly message (not an error) on 404 — the node never ran", () => {
    mockUseRunNodeLog.mockReturnValue({
      data: null,
      isPending: false,
      isError: true,
      error: new ApiError(404, "Node not found"),
    });
    mockUseRunStateHistory.mockReturnValue({ data: [] });
    mockUseAgent.mockReturnValue({ data: undefined });
    mockUseRunNodeExplain.mockReturnValue({ data: null, isLoading: false, isError: false });
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
    mockUseAgent.mockReturnValue({ data: undefined });
    mockUseRunNodeExplain.mockReturnValue({ data: null, isLoading: false, isError: false });
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
  });
});

describe("NodeDetailPanel — agent identity footer (#724)", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
    mockUseAgent.mockReset();
    mockUseRunNodeExplain.mockReset();
    mockPush.mockReset();
    mockSetPrefill.mockReset();
  });

  it("renders agent name + role + linked path instead of the raw UUID", () => {
    mockLog(
      makeLog({ agent_id: "agent-9", runtime_id: "claude-code" }),
      [],
      makeAgent({ id: "agent-9", name: "PR reviewer", role: "verifier" }),
    );
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    const link = screen.getByRole("link", { name: /PR reviewer/i });
    expect(link).toHaveAttribute("href", "/agents/agent-9");
    expect(screen.getByText(/\(verifier\)/)).toBeInTheDocument();
    // The raw UUID is no longer surfaced as a fallback string.
    expect(screen.queryByText(/agent-9$/)).toBeNull();
  });

  it("shows a 'Cortex' tag when the agent's runtime_config.callable_path starts with 'cortex.'", () => {
    mockLog(
      makeLog({ runtime_id: "python-func" }),
      [],
      makeAgent({
        runtime_id: "python-func",
        runtime_config: { callable_path: "cortex.nodes.coder:run" } as Record<string, unknown>,
      }),
    );
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    expect(screen.getByText(/^cortex$/i)).toBeInTheDocument();
  });

  it("does NOT show the Cortex tag for plain python-func agents", () => {
    mockLog(
      makeLog({ runtime_id: "python-func" }),
      [],
      makeAgent({
        runtime_id: "python-func",
        runtime_config: { callable_path: "myproject.callable:run" } as Record<string, unknown>,
      }),
    );
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    expect(screen.queryByText(/^cortex$/i)).toBeNull();
  });

  it("falls back to a short UUID prefix when the agent hasn't resolved", () => {
    mockLog(makeLog({ agent_id: "agent-aaaabbbb-cccc-dddd-eeee-ffffffffffff" }), [], undefined);
    mockUseAgent.mockReturnValue({ data: undefined });
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    // Short prefix shown (8 chars + ellipsis) — never the full UUID.
    expect(screen.getByText(/agent-aa…/)).toBeInTheDocument();
    expect(screen.queryByText(/ffffffffffff/)).toBeNull();
  });
});

describe("NodeDetailPanel — recorded prompt for cortex callables (#724)", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
    mockUseAgent.mockReset();
    mockUseRunNodeExplain.mockReset();
  });

  it("shows the Prompt tab for a python-func node when output_json carries __prompt", async () => {
    const user = userEvent.setup();
    mockLog(
      makeLog({
        runtime_id: "python-func",
        prompt_xml: "",
        output_json: { __prompt: "system: you are coder\nuser: implement this" } as Record<string, unknown>,
      }),
    );
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    // Prompt tab now visible even though runtime is python-func.
    const promptTab = screen.getByRole("tab", { name: /prompt/i });
    expect(promptTab).toBeInTheDocument();
    await user.click(promptTab);
    expect(screen.getByText(/you are coder/)).toBeInTheDocument();
    expect(
      screen.getByText(/recorded by the callable/i),
    ).toBeInTheDocument();
  });

  it("still hides the Prompt tab for a python-func node with no recorded prompt", () => {
    mockLog(
      makeLog({
        runtime_id: "python-func",
        prompt_xml: "",
        output_json: { other_key: "no prompt here" } as Record<string, unknown>,
      }),
    );
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    expect(screen.queryByRole("tab", { name: /prompt/i })).toBeNull();
  });
});

describe("NodeDetailPanel — Open in agent tester (#724)", () => {
  beforeEach(() => {
    mockUseRunNodeLog.mockReset();
    mockUseRunStateHistory.mockReset();
    mockUseAgent.mockReset();
    mockUseRunNodeExplain.mockReset();
    mockPush.mockReset();
    mockSetPrefill.mockReset();
  });

  it("stashes the input state and navigates to the agent's edit page with ?tab=test", async () => {
    const user = userEvent.setup();
    const beforeState = { run_id: "r1", repo: "lagowski/dap", extensions: { issue_number: 42 } } as Record<string, unknown>;
    const snapshots: StateSnapshot[] = [
      { id: "s0", run_id: "r1", node_id: "prev", timestamp: "2026-01-01T00:00:00Z", state: beforeState as never },
      { id: "s1", run_id: "r1", node_id: "n1", timestamp: "2026-01-01T00:00:01Z", state: { ...beforeState, extra: 1 } as never },
    ];
    mockLog(
      makeLog({ agent_id: "agent-9" }),
      snapshots,
      makeAgent({ id: "agent-9", name: "Coder", role: "developer" }),
    );
    const onOpenChange = vi.fn();
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={onOpenChange} />);

    await user.click(screen.getByRole("button", { name: /open in agent tester/i }));

    expect(mockSetPrefill).toHaveBeenCalledTimes(1);
    const payload = mockSetPrefill.mock.calls[0][0];
    expect(payload.target).toBe("agent-test");
    expect(payload.values.agent_id).toBe("agent-9");
    // ``input_state`` is the *before* snapshot, not the *after*.
    expect(payload.values.input_state).toMatchObject({
      run_id: "r1",
      extensions: { issue_number: 42 },
    });
    expect(mockPush).toHaveBeenCalledWith("/agents/agent-9/edit?tab=test");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("hides the 'Open in agent tester' button when the agent hasn't resolved", () => {
    mockLog(makeLog(), [], undefined);
    mockUseAgent.mockReturnValue({ data: undefined });
    render(<NodeDetailPanel runId="r1" nodeId="n1" onOpenChange={() => {}} />);

    expect(screen.queryByRole("button", { name: /open in agent tester/i })).toBeNull();
  });
});
