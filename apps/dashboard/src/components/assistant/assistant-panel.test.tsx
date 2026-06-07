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
});
