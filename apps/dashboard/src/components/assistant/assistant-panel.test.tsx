import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockMutate = vi.fn();
vi.mock("@/hooks/api", () => ({
  useAssistantChat: () => ({
    mutate: mockMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/agents/new",
}));

import { AssistantPanel } from "./assistant-panel";

describe("AssistantPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockMutate.mockReset();
  });

  it("renders a floating toggle when closed", () => {
    render(<AssistantPanel />);
    expect(
      screen.getByRole("button", { name: /open configuration assistant/i }),
    ).toBeInTheDocument();
    // Drawer not mounted yet.
    expect(screen.queryByText(/config assistant/i)).toBeNull();
  });

  it("opens the drawer with the empty-state prompt on click", async () => {
    const user = userEvent.setup();
    render(<AssistantPanel />);
    await user.click(
      screen.getByRole("button", { name: /open configuration assistant/i }),
    );
    expect(screen.getByText(/config assistant/i)).toBeInTheDocument();
    expect(screen.getByText(/describe what you want to build/i)).toBeInTheDocument();
  });

  it("sends a message and renders the user turn", async () => {
    const user = userEvent.setup();
    render(<AssistantPanel />);
    await user.click(
      screen.getByRole("button", { name: /open configuration assistant/i }),
    );
    const box = screen.getByRole("textbox", { name: /message the assistant/i });
    await user.type(box, "an agent that reviews PRs");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(screen.getByText("an agent that reviews PRs")).toBeInTheDocument();
  });

  it("sends the current route as context (#689 phase 2)", async () => {
    const user = userEvent.setup();
    render(<AssistantPanel />);
    await user.click(
      screen.getByRole("button", { name: /open configuration assistant/i }),
    );
    await user.type(
      screen.getByRole("textbox", { name: /message the assistant/i }),
      "help",
    );
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    const [vars] = mockMutate.mock.calls[0];
    expect(vars.context).toMatchObject({ route: "/agents/new" });
  });
});

describe("AssistantPanel actions (#689 slice 3)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockMutate.mockReset();
    mockPush.mockReset();
  });

  it("renders navigate/prefill buttons and doc links from the reply", async () => {
    const user = userEvent.setup();
    // mutate immediately resolves with an assistant reply carrying actions.
    mockMutate.mockImplementation((_vars, opts) =>
      opts.onSuccess({
        message: { role: "assistant", content: "Use api-call + haiku." },
        grounded: true,
        citations: [],
        actions: [
          { kind: "navigate", label: "Create this agent", href: "/agents/new" },
          { kind: "doc", label: "Runtimes", href: "/docs/runtimes.md" },
          {
            kind: "prefill",
            label: "Use these values",
            target: "agent",
            values: { name: "PR reviewer", role: "verifier" },
          },
        ],
      }),
    );
    render(<AssistantPanel />);
    await user.click(
      screen.getByRole("button", { name: /open configuration assistant/i }),
    );
    await user.type(
      screen.getByRole("textbox", { name: /message the assistant/i }),
      "cheap reviewer",
    );
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    // All three actions render: navigate + prefill buttons, doc as a link.
    expect(screen.getByRole("button", { name: /use these values/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /runtimes/i })).toHaveAttribute(
      "href",
      "/docs/runtimes.md",
    );
    // Clicking navigate routes (and closes the panel, so do it last).
    await user.click(screen.getByRole("button", { name: /create this agent/i }));
    expect(mockPush).toHaveBeenCalledWith("/agents/new");
  });

  it("ignores navigate actions whose href is not a same-origin path (#727)", async () => {
    const user = userEvent.setup();
    // The LLM proposes a navigate action with a protocol-relative URL — the
    // classic open-redirect vector. The handler must refuse to route.
    mockMutate.mockImplementation((_vars, opts) =>
      opts.onSuccess({
        message: { role: "assistant", content: "Sure." },
        grounded: true,
        citations: [],
        actions: [
          { kind: "navigate", label: "Take me there", href: "//evil.example" },
          { kind: "navigate", label: "Or here", href: "https://evil.example/x" },
        ],
      }),
    );
    render(<AssistantPanel />);
    await user.click(
      screen.getByRole("button", { name: /open configuration assistant/i }),
    );
    await user.type(
      screen.getByRole("textbox", { name: /message the assistant/i }),
      "anything",
    );
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    await user.click(screen.getByRole("button", { name: /take me there/i }));
    await user.click(screen.getByRole("button", { name: /or here/i }));

    // Neither button should have triggered a navigation.
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("renders doc links with rel='noopener noreferrer' (#727)", async () => {
    const user = userEvent.setup();
    mockMutate.mockImplementation((_vars, opts) =>
      opts.onSuccess({
        message: { role: "assistant", content: "Docs here." },
        grounded: true,
        citations: [],
        actions: [
          { kind: "doc", label: "Runtimes", href: "/docs/runtimes.md" },
        ],
      }),
    );
    render(<AssistantPanel />);
    await user.click(
      screen.getByRole("button", { name: /open configuration assistant/i }),
    );
    await user.type(
      screen.getByRole("textbox", { name: /message the assistant/i }),
      "where are the runtimes docs",
    );
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    const link = screen.getByRole("link", { name: /runtimes/i });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
