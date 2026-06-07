import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import {
  AssistantPageContextProvider,
  useAssistantPageContext,
  usePublishAssistantContext,
} from "./assistant-context";
import { agentFormAssistantContext } from "@/components/agents/agent-form/types";

function Reader() {
  const { context } = useAssistantPageContext();
  return <div data-testid="ctx">{JSON.stringify(context)}</div>;
}

function Publisher({ value }: { value: Record<string, unknown> | null }) {
  usePublishAssistantContext(value);
  return null;
}

function Harness() {
  const [mounted, setMounted] = useState(true);
  const [name, setName] = useState("alpha");
  return (
    <AssistantPageContextProvider>
      {mounted && <Publisher value={{ page: "agent-form", name }} />}
      <Reader />
      <button onClick={() => setName("beta")}>rename</button>
      <button onClick={() => setMounted(false)}>unmount</button>
    </AssistantPageContextProvider>
  );
}

describe("assistant page context (#689 phase 2)", () => {
  it("publishes, updates, and clears the page context", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    expect(screen.getByTestId("ctx")).toHaveTextContent('"name":"alpha"');

    await user.click(screen.getByRole("button", { name: "rename" }));
    expect(screen.getByTestId("ctx")).toHaveTextContent('"name":"beta"');

    await user.click(screen.getByRole("button", { name: "unmount" }));
    // Cleared on unmount — the assistant no longer sees stale page state.
    expect(screen.getByTestId("ctx")).toHaveTextContent("null");
  });

  it("reading outside the provider is a safe no-op", () => {
    render(<Reader />);
    expect(screen.getByTestId("ctx")).toHaveTextContent("null");
  });
});

describe("agentFormAssistantContext", () => {
  it("returns minimal context when no values yet", () => {
    expect(agentFormAssistantContext(undefined, "new")).toEqual({
      page: "agent-form",
      mode: "new",
    });
  });

  it("extracts non-secret fields and never the prompt body", () => {
    const ctx = agentFormAssistantContext(
      {
        name: "PR reviewer",
        role: "verifier",
        runtime_id: "api-call",
        prompt_template: "<agent_prompt>secret-ish instructions</agent_prompt>",
        runtime_config: { provider: "anthropic", model_id: "claude-haiku-4-5" },
        input_schema: ["diff"],
        output_schema: ["verdict"],
      },
      "edit",
    );
    expect(ctx).toMatchObject({
      page: "agent-form",
      mode: "edit",
      name: "PR reviewer",
      role: "verifier",
      runtime_id: "api-call",
      provider: "anthropic",
      model_id: "claude-haiku-4-5",
      has_prompt: true,
      input_schema: ["diff"],
      output_schema: ["verdict"],
    });
    // The prompt body itself is never published — only whether one exists.
    expect(JSON.stringify(ctx)).not.toContain("secret-ish instructions");
  });
});
