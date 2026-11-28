import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { PromptTemplateField } from "./prompt-template-field";

const TEMPLATE = "<agent_prompt><run_id>{{ run_id }}</run_id></agent_prompt>";

function Harness({ runtimeId }: { runtimeId: string }) {
  const { register } = useForm<{ prompt_template: string }>({
    defaultValues: { prompt_template: TEMPLATE },
  });
  return (
    <PromptTemplateField runtimeId={runtimeId} registration={register("prompt_template")} />
  );
}

describe("PromptTemplateField (#736)", () => {
  it("shows the editor directly for an LLM runtime", () => {
    render(<Harness runtimeId="claude-code" />);
    const box = screen.getByRole("textbox");
    expect(box).toHaveValue(TEMPLATE);
    expect(screen.queryByText(/not used by this runtime/i)).toBeNull();
    expect(screen.queryByText(/show template anyway/i)).toBeNull();
  });

  it("leads with a note and tucks the editor behind a disclosure for python-func", () => {
    render(<Harness runtimeId="python-func" />);
    // The misleading template is no longer the primary, prominent editor.
    expect(screen.getByText(/not used by this runtime/i)).toBeInTheDocument();
    expect(screen.getByText(/show template anyway/i)).toBeInTheDocument();
    // It stays registered/editable (schema requires it) — just disclosed.
    expect(screen.getByRole("textbox")).toHaveValue(TEMPLATE);
  });
});
