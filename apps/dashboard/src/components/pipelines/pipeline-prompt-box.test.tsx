import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PipelinePromptBox } from "./pipeline-prompt-box";
import * as exportLib from "@/lib/pipeline-export";
import type { GeneratedPipeline } from "@/lib/api/types";

const GENERATED: GeneratedPipeline = {
  name: "email-triage",
  nodes: 3,
  bundle: { schema_version: "pipeline-export/2", pipeline: { name: "Email triage" } },
};

function typePrompt(text: string) {
  fireEvent.change(screen.getByLabelText(/what should the pipeline do/i), {
    target: { value: text },
  });
}

afterEach(() => vi.restoreAllMocks());

describe("PipelinePromptBox (cortex#1774 S5)", () => {
  it("keeps Generate disabled until there is a non-blank prompt", () => {
    render(<PipelinePromptBox generate={vi.fn()} onOpenInCanvas={vi.fn()} />);
    const button = screen.getByRole("button", { name: /generate pipeline/i });
    expect(button).toBeDisabled();
    typePrompt("   ");
    expect(button).toBeDisabled();
    typePrompt("triage email");
    expect(button).toBeEnabled();
  });

  it("generates, then opens the returned bundle in the canvas", async () => {
    const generate = vi.fn().mockResolvedValue(GENERATED);
    const onOpen = vi.fn().mockResolvedValue(undefined);
    render(<PipelinePromptBox generate={generate} onOpenInCanvas={onOpen} />);

    typePrompt("  triage support email  ");
    fireEvent.click(screen.getByRole("button", { name: /generate pipeline/i }));

    expect(await screen.findByText("email-triage")).toBeInTheDocument();
    expect(screen.getByText("(3 nodes)")).toBeInTheDocument();
    expect(generate).toHaveBeenCalledWith("triage support email");

    fireEvent.click(screen.getByRole("button", { name: /open in canvas/i }));
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith(GENERATED.bundle));
  });

  it("downloads the generated bundle as JSON", async () => {
    const download = vi
      .spyOn(exportLib, "downloadPipelineBundle")
      .mockReturnValue("email-triage.pipeline-bundle.json");
    render(
      <PipelinePromptBox generate={vi.fn().mockResolvedValue(GENERATED)} onOpenInCanvas={vi.fn()} />,
    );
    typePrompt("triage");
    fireEvent.click(screen.getByRole("button", { name: /generate pipeline/i }));
    fireEvent.click(await screen.findByRole("button", { name: /download json/i }));
    expect(download).toHaveBeenCalledWith(GENERATED.bundle, "email-triage");
  });

  it("shows the engine's error and no result when generation fails", async () => {
    const generate = vi.fn().mockRejectedValue(new Error("generator refused: no valid spec"));
    render(<PipelinePromptBox generate={generate} onOpenInCanvas={vi.fn()} />);
    typePrompt("x");
    fireEvent.click(screen.getByRole("button", { name: /generate pipeline/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no valid spec/);
    expect(screen.queryByRole("button", { name: /open in canvas/i })).toBeNull();
  });

  it("shows an import failure instead of swallowing it", async () => {
    const onOpen = vi.fn().mockRejectedValue(new Error("agent missing"));
    render(
      <PipelinePromptBox generate={vi.fn().mockResolvedValue(GENERATED)} onOpenInCanvas={onOpen} />,
    );
    typePrompt("x");
    fireEvent.click(screen.getByRole("button", { name: /generate pipeline/i }));
    fireEvent.click(await screen.findByRole("button", { name: /open in canvas/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/agent missing/);
  });
});
