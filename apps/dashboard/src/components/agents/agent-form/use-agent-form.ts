"use client";

/**
 * State + side-effects bundle for the agent form (audit D1 split).
 *
 * Centralises the four moving pieces the orchestrator would
 * otherwise interleave with its JSX render:
 *
 * 1. ``react-hook-form`` controller + Zod-validated form shape
 *    (name / role / runtime_id / prompt_template).
 * 2. ``runtimeConfig`` state + ``runtimeConfigJsonValid`` gate
 *    — these sit *outside* the Zod schema because the shape is
 *    runtime-specific (api-call vs cli-process vs ...).
 * 3. ``inputSchema`` / ``outputSchema`` state plus the
 *    ``touched`` refs that decide whether switching role
 *    re-seeds the pickers (only while the user hasn't edited
 *    them — once they do, we stop overwriting their work).
 * 4. The three useEffects: snapshot emission for sibling
 *    components, runtime-id → defaults reset, and role →
 *    schema mirroring.
 *
 * The orchestrator consumes a flat ``{ form, runtimeConfig, ... }``
 * surface and renders the JSX. No behaviour change vs pre-split.
 */

import { useEffect, useRef, useState } from "react";
import { useForm, type UseFormReturn } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  ROLE_DEFAULT_INPUT_SCHEMA,
  ROLE_DEFAULT_OUTPUT_SCHEMA,
} from "@/lib/pipeline-state-fields";

import {
  defaultRuntimeConfig,
  pruneRuntimeConfig,
  validateRuntimeConfig,
} from "../runtime-config-schemas";
import {
  DEFAULT_PROMPT_TEMPLATE,
  formSchema,
  ROLES,
  type AgentFormValues,
  type FormShape,
} from "./types";


interface UseAgentFormParams {
  initialValues?: Partial<AgentFormValues>;
  onSubmit: (values: AgentFormValues) => Promise<unknown>;
  onValuesChange?: (snapshot: { values: AgentFormValues; valid: boolean }) => void;
}


interface UseAgentFormResult {
  form: UseFormReturn<FormShape>;
  runtimeConfig: Record<string, unknown>;
  setRuntimeConfig: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
  runtimeConfigJsonValid: boolean;
  setRuntimeConfigJsonValid: React.Dispatch<React.SetStateAction<boolean>>;
  inputSchema: string[];
  outputSchema: string[];
  setInputSchema: React.Dispatch<React.SetStateAction<string[]>>;
  setOutputSchema: React.Dispatch<React.SetStateAction<string[]>>;
  handleInputSchemaChange: (next: string[]) => void;
  handleOutputSchemaChange: (next: string[]) => void;
  /** Cleared internally when the user explicitly opts back into role defaults. */
  inputSchemaTouched: React.MutableRefObject<boolean>;
  outputSchemaTouched: React.MutableRefObject<boolean>;
  watchedRuntimeId: string;
  watchedRole: string;
  handleSubmit: (e?: React.BaseSyntheticEvent) => Promise<void>;
}


export function useAgentForm({
  initialValues,
  onSubmit,
  onValuesChange,
}: UseAgentFormParams): UseAgentFormResult {
  const form = useForm<FormShape>({
    resolver: zodResolver(formSchema),
    // ``onChange`` so ``formState.isValid`` reflects the live form state.
    // The default ``onSubmit`` mode keeps ``isValid`` stuck at ``false``
    // until the user clicks Submit at least once — which would leave the
    // dry-run Test panel disabled even on a perfectly valid form.
    mode: "onChange",
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
  const [inputSchema, setInputSchema] = useState<string[]>(() => {
    if (initialValues?.input_schema !== undefined) {
      return [...initialValues.input_schema];
    }
    return [...(ROLE_DEFAULT_INPUT_SCHEMA[initialRole] ?? [])];
  });
  const [outputSchema, setOutputSchema] = useState<string[]>(() => {
    if (initialValues?.output_schema !== undefined) {
      return [...initialValues.output_schema];
    }
    return [...(ROLE_DEFAULT_OUTPUT_SCHEMA[initialRole] ?? [])];
  });

  // Track whether the user has ever touched each picker. If not,
  // switching roles updates the seed; once they edit, we stop
  // overriding their selection on role change.
  const inputSchemaTouched = useRef(initialValues?.input_schema !== undefined);
  const handleInputSchemaChange = (next: string[]) => {
    inputSchemaTouched.current = true;
    setInputSchema(next);
  };
  const outputSchemaTouched = useRef(initialValues?.output_schema !== undefined);
  const handleOutputSchemaChange = (next: string[]) => {
    outputSchemaTouched.current = true;
    setOutputSchema(next);
  };

  const watchedRuntimeId = form.watch("runtime_id");
  const watchedRole = form.watch("role");
  const watchedName = form.watch("name");
  const watchedPrompt = form.watch("prompt_template");

  // Emit the current form snapshot to interested siblings (e.g. the
  // dry-run Test panel). ``valid`` mirrors the Submit gate: zod schema
  // + per-runtime required fields + parseable JSON inputs.
  useEffect(() => {
    if (!onValuesChange) return;
    const runtimeErrors = validateRuntimeConfig(watchedRuntimeId, runtimeConfig);
    const valid =
      form.formState.isValid &&
      runtimeConfigJsonValid &&
      runtimeErrors.length === 0;
    onValuesChange({
      values: {
        name: watchedName,
        role: watchedRole,
        runtime_id: watchedRuntimeId,
        prompt_template: watchedPrompt,
        runtime_config: pruneRuntimeConfig(watchedRuntimeId, runtimeConfig),
        input_schema: inputSchema,
        output_schema: outputSchema,
      },
      valid,
    });
  }, [
    onValuesChange,
    watchedName,
    watchedRole,
    watchedRuntimeId,
    watchedPrompt,
    runtimeConfig,
    runtimeConfigJsonValid,
    inputSchema,
    outputSchema,
    form.formState.isValid,
  ]);

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

  // Mirror role → input/output_schema seed *until* the user edits the
  // picker. After that, switching roles never silently overwrites
  // their work. Independent ``touched`` refs let one picker stay in
  // sync while the other holds custom values.
  useEffect(() => {
    if (!watchedRole) return;
    if (!inputSchemaTouched.current) {
      const seed = ROLE_DEFAULT_INPUT_SCHEMA[watchedRole] ?? [];
      setInputSchema([...seed]);
    }
    if (!outputSchemaTouched.current) {
      const seed = ROLE_DEFAULT_OUTPUT_SCHEMA[watchedRole] ?? [];
      setOutputSchema([...seed]);
    }
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

  return {
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
  };
}
