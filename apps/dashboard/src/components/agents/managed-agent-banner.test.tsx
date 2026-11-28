import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ManagedAgentBanner } from "./managed-agent-banner";

describe("ManagedAgentBanner (#739)", () => {
  it("renders for a cortex agent with the callable + a link to runs", () => {
    render(
      <ManagedAgentBanner
        agent={{
          runtime_id: "python-func",
          runtime_config: { callable_path: "cortex.nodes.mockup:run" },
        }}
      />,
    );
    expect(screen.getByText(/managed by cortex/i)).toBeInTheDocument();
    expect(screen.getByText("cortex.nodes.mockup:run")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /recent run/i })).toHaveAttribute("href", "/runs");
  });

  it("renders nothing for an ordinary agent", () => {
    const { container } = render(
      <ManagedAgentBanner agent={{ runtime_id: "claude-code", runtime_config: {} }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
