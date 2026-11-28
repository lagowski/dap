/**
 * Shared types and constants for the agent form (audit D1 split).
 *
 * Lives here rather than next to the orchestrator so the form,
 * the ``use-agent-form`` hook, and external callers (parent pages
 * that pass ``initialValues`` / consume ``onValuesChange``) can
 * import the same definitions without circular deps.
 */

import { z } from "zod";


export const ROLES = [
  "task_selector",
  "prompt_builder",
  "test_author",
  "implementer",
  "verifier",
  "post_check",
] as const;


export const formSchema = z.object({
  name: z.string().min(1, "Name is required").max(200),
  role: z.string().min(1, "Role is required"),
  runtime_id: z.string().min(1, "Runtime is required"),
  prompt_template: z
    .string()
    .min(1, "Prompt template is required")
    .refine(
      (s) => s.includes("<agent_prompt"),
      "Template should contain <agent_prompt> root element",
    ),
});


export type FormShape = z.infer<typeof formSchema>;


/**
 * Public form values include the typed runtime_config + per-agent
 * input/output contracts so callers can send a complete payload to
 * AgentCreate / AgentUpdate without rebuilding it.
 */
export interface AgentFormValues extends FormShape {
  runtime_config: Record<string, unknown>;
  input_schema: string[];
  output_schema: string[];
}


/**
 * Build the **non-secret** assistant context (#689 phase 2) for an agent form.
 * Names + shape + config scalars only — provider/model are not secrets (the
 * keys live in env vars), and we publish ``has_prompt`` rather than the prompt
 * body. Lets the assistant tailor advice to the half-filled form.
 */
export function agentFormAssistantContext(
  values: AgentFormValues | undefined,
  mode: "new" | "clone" | "edit",
): Record<string, unknown> {
  if (!values) return { page: "agent-form", mode };
  const cfg = values.runtime_config ?? {};
  const asString = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
  return {
    page: "agent-form",
    mode,
    name: asString(values.name),
    role: asString(values.role),
    runtime_id: asString(values.runtime_id),
    provider: asString(cfg.provider),
    model_id: asString(cfg.model_id),
    has_prompt: Boolean(values.prompt_template?.trim()),
    input_schema: values.input_schema ?? [],
    output_schema: values.output_schema ?? [],
  };
}


export const DEFAULT_PROMPT_TEMPLATE = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <task>
    Describe the task for this agent.
  </task>
</agent_prompt>`;
