import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PythonFuncPromptNote } from "./python-func-prompt-note";

describe("PythonFuncPromptNote (#736)", () => {
  it("explains the template is inert and links to a run to see the real prompt", () => {
    render(<PythonFuncPromptNote callablePath="cortex.dap_steps.human_gate:noop" />);
    expect(screen.getByText(/not used by this runtime/i)).toBeInTheDocument();
    expect(screen.getByText("cortex.dap_steps.human_gate:noop")).toBeInTheDocument();
    expect(screen.getByText(/extensions\.__audit/)).toBeInTheDocument();
    // The actionable bit: a link to the runs list where the recorded prompt lives.
    expect(screen.getByRole("link", { name: /recent run/i })).toHaveAttribute("href", "/runs");
  });

  it("renders without a callable path", () => {
    render(<PythonFuncPromptNote />);
    expect(screen.getByText(/not used by this runtime/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /recent run/i })).toHaveAttribute("href", "/runs");
  });
});
