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


export const DEFAULT_PROMPT_TEMPLATE = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <task>
    Describe the task for this agent.
  </task>
</agent_prompt>`;
