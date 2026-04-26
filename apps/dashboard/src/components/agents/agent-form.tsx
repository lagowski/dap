"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formatApiError } from "@/lib/api/client";

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

export type AgentFormValues = z.infer<typeof formSchema>;

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
  const form = useForm<AgentFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: initialValues?.name ?? "",
      role: initialValues?.role ?? ROLES[0],
      runtime_id: initialValues?.runtime_id ?? "api-call",
      prompt_template: initialValues?.prompt_template ?? DEFAULT_PROMPT_TEMPLATE,
    },
  });

  const handleSubmit = form.handleSubmit(async (values) => {
    await onSubmit(values);
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
        <Button type="submit" disabled={isPending}>
          {isPending ? "Saving…" : submitLabel}
        </Button>
        <Button type="button" variant="outline" asChild>
          <Link href={cancelHref}>Cancel</Link>
        </Button>
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
