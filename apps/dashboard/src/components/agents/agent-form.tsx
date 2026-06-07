"use client";

/**
 * Agent create / edit form orchestrator (audit D1 slice 7).
 *
 * Post-split this file owns only the JSX render. The
 * react-hook-form controller, all derived state, and the three
 * useEffects that keep schemas in sync now live in
 * ``use-agent-form``. Two presentational helpers (``Field``,
 * ``RoleDefaultsHint``) live as siblings.
 *
 * Public surface kept identical: external callers still
 * ``import { AgentForm, type AgentFormValues } from
 * "@/components/agents/agent-form"`` and
 * ``DEFAULT_PROMPT_TEMPLATE`` is re-exported for anyone who needs
 * it.
 *
 * Pre-split this file was 455 LOC; post-split it stays under 180.
 */

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatApiError } from "@/lib/api/client";
import {
  ROLE_DEFAULT_INPUT_SCHEMA,
  ROLE_DEFAULT_OUTPUT_SCHEMA,
} from "@/lib/pipeline-state-fields";

import { PipelineStateFieldPicker } from "./pipeline-state-field-picker";
import { RuntimeConfigEditor } from "./runtime-config-editor";
import { AGENT_RUNTIME_IDS } from "./runtime-config-schemas";

import { Field } from "./agent-form/field";
import { PromptTemplateField } from "./agent-form/prompt-template-field";
import { RoleDefaultsHint } from "./agent-form/role-defaults-hint";
import { ROLES, type AgentFormValues } from "./agent-form/types";
import { useAgentForm } from "./agent-form/use-agent-form";

export { DEFAULT_PROMPT_TEMPLATE } from "./agent-form/types";
export type { AgentFormValues } from "./agent-form/types";


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
  /**
   * Optional snapshot of the live form values. Called on every change
   * so a sibling component (e.g. the dry-run Test panel) can read the
   * current draft without lifting state. Validity reflects the same
   * gate as ``Submit`` — runtime_config required fields + JSON parse
   * status — so the Test panel can disable "Run test" for the same
   * reasons Submit is disabled.
   */
  onValuesChange?: (snapshot: { values: AgentFormValues; valid: boolean }) => void;
}


export function AgentForm({
  initialValues,
  onSubmit,
  isPending,
  submitError,
  submitLabel,
  cancelHref = "/agents",
  lockedFields = [],
  onValuesChange,
}: AgentFormProps) {
  const {
    form,
    runtimeConfig,
    setRuntimeConfig,
    runtimeConfigJsonValid,
    setRuntimeConfigJsonValid,
    inputSchema,
    outputSchema,
    setInputSchema,
    setOutputSchema,
    handleInputSchemaChange,
    handleOutputSchemaChange,
    inputSchemaTouched,
    outputSchemaTouched,
    watchedRuntimeId,
    watchedRole,
    handleSubmit,
  } = useAgentForm({ initialValues, onSubmit, onValuesChange });

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
          {AGENT_RUNTIME_IDS.map((r) => (
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

      <Field label="Inputs (read by the prompt)">
        <RoleDefaultsHint
          role={watchedRole}
          recommended={ROLE_DEFAULT_INPUT_SCHEMA[watchedRole]}
          current={inputSchema}
          onUseDefaults={(next) => {
            // Clearing ``touched`` puts the picker back into "follow
            // role" mode — the user explicitly opted into defaults,
            // so subsequent role changes should keep mirroring. Once
            // they edit the picker manually, ``handleInputSchemaChange``
            // flips ``touched`` back to true and mirroring stops.
            inputSchemaTouched.current = false;
            setInputSchema(next);
          }}
          subjectLabel="inputs"
        />
        <PipelineStateFieldPicker
          value={inputSchema}
          onChange={handleInputSchemaChange}
          description="PipelineState fields the prompt can reference via Jinja {{ field_name }}. PipelineState is the shared data bag flowing through every node — this picker selects the subset your agent reads. Empty = legacy mode (template sees the full state)."
        />
      </Field>

      <Field label="Outputs (written back to PipelineState)">
        <RoleDefaultsHint
          role={watchedRole}
          recommended={ROLE_DEFAULT_OUTPUT_SCHEMA[watchedRole]}
          current={outputSchema}
          onUseDefaults={(next) => {
            // Same rationale as the Inputs callback above.
            outputSchemaTouched.current = false;
            setOutputSchema(next);
          }}
          subjectLabel="outputs"
        />
        <PipelineStateFieldPicker
          value={outputSchema}
          onChange={handleOutputSchemaChange}
          description="PipelineState fields this role's textual output is parsed and validated against before downstream nodes see them. Documents the expected state keys for the role's response; adapter-supplied structured telemetry (token counts, exit codes, etc.) is merged into PipelineState separately by the engine. Empty = legacy mode (engine falls back to ROLE_FIELDS for known roles)."
        />
      </Field>

      <PromptTemplateField
        runtimeId={watchedRuntimeId}
        registration={form.register("prompt_template")}
        error={form.formState.errors.prompt_template?.message}
      />

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
