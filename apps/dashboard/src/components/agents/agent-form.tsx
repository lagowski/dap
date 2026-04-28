"use client";

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formatApiError } from "@/lib/api/client";
import { ROLE_DEFAULT_OUTPUT_SCHEMA } from "@/lib/pipeline-state-fields";
import { PipelineStateFieldPicker } from "./pipeline-state-field-picker";
import { RuntimeConfigEditor } from "./runtime-config-editor";
import {
  defaultRuntimeConfig,
  pruneRuntimeConfig,
  validateRuntimeConfig,
} from "./runtime-config-schemas";

const RUNTIMES = [
  "api-call",
  "claude-code",
  "gemini-cli",
  "codex",
  "aider",
  "bash",
  "http",
] as const;

const ROLES = [
  "task_selector",
  "prompt_builder",
  "test_author",
  "implementer",
  "verifier",
  "post_check",
] as const;

const formSchema = z.object({
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

type FormShape = z.infer<typeof formSchema>;

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

interface AgentFormProps {
  /** Pre-fill values when editing; omit for create. */
  initialValues?: Partial<AgentFormValues>;
  /**
   * What to do on valid submit. Throwing/returning a rejected promise
   * surfaces as `submitError`.
   */
  onSubmit: (values: AgentFormValues) => Promise<unknown>;
  /** Pending state from the parent's mutation. */
  isPending: boolean;
  /** Error from the parent's mutation, formatted via formatApiError. */
  submitError?: unknown;
  /** Label for the primary action — "Create agent" / "Save v3". */
  submitLabel: string;
  /** Where Cancel returns to — defaults to /agents. */
  cancelHref?: string;
  /**
   * Lock the role/runtime selectors. New agents pick them once, but on
   * update the engine treats role as immutable; runtime can change but
   * usually shouldn't (would change cost/behaviour materially).
   */
  lockedFields?: ReadonlyArray<"role" | "runtime_id">;
}

export function AgentForm({
  initialValues,
  onSubmit,
  isPending,
  submitError,
  submitLabel,
  cancelHref = "/agents",
  lockedFields = [],
}: AgentFormProps) {
  const form = useForm<FormShape>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: initialValues?.name ?? "",
      role: initialValues?.role ?? ROLES[0],
      runtime_id: initialValues?.runtime_id ?? "api-call",
      prompt_template: initialValues?.prompt_template ?? DEFAULT_PROMPT_TEMPLATE,
    },
  });

  // runtime_config lives outside the Zod schema (free-form per runtime).
  // Initialised from props or from the runtime's declared defaults so a
  // fresh agent has the required fields populated.
  const initialRuntimeId = initialValues?.runtime_id ?? "api-call";
  const [runtimeConfig, setRuntimeConfig] = useState<Record<string, unknown>>(
    () =>
      initialValues?.runtime_config ?? defaultRuntimeConfig(initialRuntimeId),
  );
  // The editor reports false when JSON inputs (Advanced mode or per-field
  // json) fail to parse. We block submit while invalid so the user can't
  // ship a payload that doesn't match the textarea contents.
  const [runtimeConfigJsonValid, setRuntimeConfigJsonValid] = useState(true);

  // input_schema / output_schema also sit outside the Zod schema. For new
  // agents we seed output_schema from ROLE_DEFAULT_OUTPUT_SCHEMA — saves
  // the user a round of clicking when the role's contract is conventional.
  // Edit-mode pre-fills from server values; if the user changes the role
  // we leave the picker alone (don't clobber their work).
  const initialRole = initialValues?.role ?? ROLES[0];
  const [inputSchema, setInputSchema] = useState<string[]>(
    () => [...(initialValues?.input_schema ?? [])],
  );
  const [outputSchema, setOutputSchema] = useState<string[]>(() => {
    if (initialValues?.output_schema !== undefined) {
      return [...initialValues.output_schema];
    }
    return [...(ROLE_DEFAULT_OUTPUT_SCHEMA[initialRole] ?? [])];
  });

  // Track whether the user has ever touched the output_schema picker.
  // If not, switching roles updates the seed; once they edit, we stop
  // overriding their selection on role change.
  const outputSchemaTouched = useRef(initialValues?.output_schema !== undefined);
  const handleOutputSchemaChange = (next: string[]) => {
    outputSchemaTouched.current = true;
    setOutputSchema(next);
  };

  const watchedRuntimeId = form.watch("runtime_id");
  const watchedRole = form.watch("role");

  // When the user switches runtimes, swap to that runtime's defaults so
  // required fields aren't left blank from the previous selection.
  // Reset is only triggered by user-driven changes to runtime_id, not by
  // the initial render.
  useEffect(() => {
    if (watchedRuntimeId && watchedRuntimeId !== initialRuntimeId) {
      setRuntimeConfig(defaultRuntimeConfig(watchedRuntimeId));
      setRuntimeConfigJsonValid(true);
    }
  }, [watchedRuntimeId, initialRuntimeId]);

  // Mirror role → output_schema seed *until* the user edits the picker.
  // After that, switching roles never silently overwrites their work.
  useEffect(() => {
    if (outputSchemaTouched.current) return;
    if (!watchedRole) return;
    const seed = ROLE_DEFAULT_OUTPUT_SCHEMA[watchedRole] ?? [];
    setOutputSchema([...seed]);
  }, [watchedRole]);

  const handleSubmit = form.handleSubmit(async (values) => {
    // Block submit if the runtime config is missing required fields or
    // if any JSON input is unparseable. The engine would reject with 422;
    // we surface it inline first.
    const runtimeErrors = validateRuntimeConfig(values.runtime_id, runtimeConfig);
    if (runtimeErrors.length > 0 || !runtimeConfigJsonValid) {
      // Editor renders per-field messages; nothing more to do here.
      return;
    }
    try {
      await onSubmit({
        ...values,
        runtime_config: pruneRuntimeConfig(values.runtime_id, runtimeConfig),
        input_schema: inputSchema,
        output_schema: outputSchema,
      });
    } catch {
      // intentional: parent already shows the error via submitError
    }
  });

  const isLocked = (field: "role" | "runtime_id") => lockedFields.includes(field);

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <Field label="Name" error={form.formState.errors.name?.message}>
        <Input {...form.register("name")} placeholder="Test Author" />
      </Field>

      <Field label="Role" error={form.formState.errors.role?.message}>
        <select
          {...form.register("role")}
          disabled={isLocked("role")}
          className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Runtime" error={form.formState.errors.runtime_id?.message}>
        <select
          {...form.register("runtime_id")}
          disabled={isLocked("runtime_id")}
          className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
        >
          {RUNTIMES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Runtime configuration">
        <RuntimeConfigEditor
          runtime_id={watchedRuntimeId}
          value={runtimeConfig}
          onChange={setRuntimeConfig}
          onValidityChange={setRuntimeConfigJsonValid}
        />
      </Field>

      <Field label="Inputs">
        <PipelineStateFieldPicker
          value={inputSchema}
          onChange={setInputSchema}
          description="Fields the agent's prompt template can reference. Empty = legacy mode (template sees the full PipelineState)."
        />
      </Field>

      <Field label="Outputs">
        <PipelineStateFieldPicker
          value={outputSchema}
          onChange={handleOutputSchemaChange}
          description="Fields the agent's response is allowed to write. Empty = legacy mode (engine falls back to ROLE_FIELDS for known roles)."
        />
      </Field>

      <Field
        label="Prompt template (Jinja2 → XML)"
        error={form.formState.errors.prompt_template?.message}
      >
        <Textarea
          {...form.register("prompt_template")}
          rows={10}
          className="font-mono text-xs"
        />
      </Field>

      {submitError ? (
        <p className="text-sm text-destructive" role="alert">
          {formatApiError(submitError)}
        </p>
      ) : null}

      <div className="flex gap-2 pt-2">
        <Button
          type="submit"
          disabled={isPending || !runtimeConfigJsonValid}
        >
          {isPending ? "Saving…" : submitLabel}
        </Button>
        <Button type="button" variant="outline" asChild>
          <Link href={cancelHref}>Cancel</Link>
        </Button>
        {!runtimeConfigJsonValid ? (
          <span className="text-xs text-destructive self-center">
            Fix runtime_config JSON to enable submit
          </span>
        ) : null}
      </div>
    </form>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
