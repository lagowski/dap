import type { UseFormRegisterReturn } from "react-hook-form";

import { Textarea } from "@/components/ui/textarea";

import { Field } from "./field";

interface PromptTemplateFieldProps {
  runtimeId: string;
  registration: UseFormRegisterReturn;
  error?: string;
}

/**
 * The agent form's prompt-template editor (#736).
 *
 * For LLM/CLI runtimes it's the primary editor. For ``python-func`` agents the
 * template is never rendered — the agent runs a Python callable, not an LLM —
 * so showing a large editable textarea (pre-filled with a schema-required
 * placeholder) reads as a live prompt and misleads. There we lead with a note
 * and tuck the still-editable field behind a disclosure so it stays registered
 * (the schema requires a non-empty ``<agent_prompt>`` value) without pretending
 * it matters. Mirrors the detail-page fix in ``PromptTemplateCard``.
 */
export function PromptTemplateField({
  runtimeId,
  registration,
  error,
}: PromptTemplateFieldProps) {
  if (runtimeId === "python-func") {
    return (
      <Field label="Prompt template (Jinja2 → XML)" error={error}>
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Not used by this runtime. <span className="font-mono">python-func</span> agents run a
            Python callable, not an LLM, so this template is never rendered — it&apos;s kept only to
            satisfy the schema, so you don&apos;t need to edit it.
          </p>
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              Show template anyway
            </summary>
            <Textarea {...registration} rows={6} className="mt-2 font-mono text-xs" />
          </details>
        </div>
      </Field>
    );
  }

  return (
    <Field label="Prompt template (Jinja2 → XML)" error={error}>
      <Textarea {...registration} rows={10} className="font-mono text-xs" />
    </Field>
  );
}
