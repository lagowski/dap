"use client";

import { useState } from "react";
import { Download, Loader2, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { formatApiError } from "@/lib/api/client";
import type { GeneratedPipeline, PipelineExport } from "@/lib/api/types";
import { downloadPipelineBundle } from "@/lib/pipeline-export";

/** Must match ``PROMPT_MAX_CHARS`` in the engine's ``api/pipeline_generator.py``. */
export const PROMPT_MAX_CHARS = 4000;

interface PipelinePromptBoxProps {
  /** ``POST /pipelines/generate``; rejects with the engine's error. */
  generate: (prompt: string) => Promise<GeneratedPipeline>;
  /** Import the generated bundle and open it in the Designer canvas. */
  onOpenInCanvas: (bundle: PipelineExport) => Promise<void> | void;
  /** Disable the box while another import (e.g. a template) is running. */
  disabled?: boolean;
}

/**
 * Prompt box on ``/pipelines/new`` (cortex#1774 S5): describe an automation,
 * the engine's configured generator returns a pipeline bundle, then open it
 * in the canvas (a normal import, so every import check applies) or download
 * the JSON. Shown only when ``GET /pipelines/generate`` reports it enabled.
 */
export function PipelinePromptBox({
  generate,
  onOpenInCanvas,
  disabled = false,
}: PipelinePromptBoxProps) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState<"generate" | "open" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GeneratedPipeline | null>(null);

  const trimmed = prompt.trim();
  const locked = disabled || busy !== null;

  const handleGenerate = async () => {
    if (!trimmed || locked) return;
    setBusy("generate");
    setError(null);
    setResult(null);
    try {
      setResult(await generate(trimmed));
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(null);
    }
  };

  const handleOpen = async () => {
    if (!result || locked) return;
    setBusy("open");
    setError(null);
    try {
      // The engine validated the envelope against the import schema
      // before returning it, so the cast only narrows the generated type.
      await onOpenInCanvas(result.bundle as unknown as PipelineExport);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby="prompt-box-title" className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center gap-1.5">
        <Wand2 className="h-4 w-4" aria-hidden="true" />
        <h2 id="prompt-box-title" className="text-base font-medium">
          Describe it in a prompt
        </h2>
      </div>
      <label htmlFor="pipeline-prompt" className="sr-only">
        What should the pipeline do?
      </label>
      <Textarea
        id="pipeline-prompt"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        maxLength={PROMPT_MAX_CHARS}
        rows={3}
        placeholder="e.g. Classify a support email, draft a reply, a human approves it"
        disabled={locked}
      />
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {prompt.length}/{PROMPT_MAX_CHARS}
        </span>
        <Button type="button" onClick={handleGenerate} disabled={!trimmed || locked}>
          {busy === "generate" ? (
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden="true" />
          ) : null}
          {busy === "generate" ? "Generating…" : "Generate pipeline"}
        </Button>
      </div>

      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      {result ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/40 p-3">
          <p className="text-sm">
            <span className="font-medium">{result.name}</span>{" "}
            <span className="text-muted-foreground">
              ({result.nodes} {result.nodes === 1 ? "node" : "nodes"})
            </span>
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => downloadPipelineBundle(result.bundle, result.name)}
            >
              <Download className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Download JSON
            </Button>
            <Button type="button" onClick={handleOpen} disabled={locked}>
              {busy === "open" ? "Opening…" : "Open in canvas"}
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
