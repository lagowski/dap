import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PromptTemplateCard } from "./agent-prompt-template-card";

const TEMPLATE = "<agent_prompt><run_id>{{ run_id }}</run_id></agent_prompt>";

describe("PromptTemplateCard (#736)", () => {
  it("renders the template for an LLM runtime", () => {
    render(
      <PromptTemplateCard
        runtimeId="claude-code"
        promptTemplate={TEMPLATE}
        runtimeConfig={{}}
        version={3}
      />,
    );
    expect(screen.getByText(/prompt template \(current — v3\)/i)).toBeInTheDocument();
    expect(screen.getByText(/run_id/)).toBeInTheDocument();
    expect(screen.queryByText(/not used by this runtime/i)).toBeNull();
  });

  it("hides the inert template for a python-func agent and explains why", () => {
    render(
      <PromptTemplateCard
        runtimeId="python-func"
        promptTemplate={TEMPLATE}
        runtimeConfig={{ callable_path: "cortex.dap_steps.human_gate:noop" }}
        version={1}
      />,
    );
    // The misleading placeholder is NOT shown as if it were a live prompt.
    expect(screen.queryByText(/{{ run_id }}/)).toBeNull();
    // A clear note explains the template isn't used, naming the callable.
    expect(screen.getByText(/not used by this runtime/i)).toBeInTheDocument();
    expect(screen.getByText("cortex.dap_steps.human_gate:noop")).toBeInTheDocument();
  });

  it("still notes 'not used' for python-func without a callable_path", () => {
    render(
      <PromptTemplateCard
        runtimeId="python-func"
        promptTemplate={TEMPLATE}
        runtimeConfig={{}}
        version={1}
      />,
    );
    expect(screen.getByText(/not used by this runtime/i)).toBeInTheDocument();
    expect(screen.queryByText(/{{ run_id }}/)).toBeNull();
  });
});
