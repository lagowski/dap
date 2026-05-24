"use client";

import { useState } from "react";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { formatApiError } from "@/lib/api/client";
import { useCurrentUser, useValidateProjectEnv } from "@/hooks/api";
import type { EnvVarValidationResult } from "@/lib/api/types";
import { EnvVarsEditor } from "@/components/projects/env-vars-editor";
import { Field } from "@/components/projects/form-field";
import { GateAutoApproveEditor } from "@/components/projects/gate-auto-approve-editor";
import { PipelineBindingsEditor } from "@/components/projects/pipeline-bindings-editor";

export { buildGateAutoApproveRows } from "@/components/projects/gate-auto-approve-editor";

const formSchema = z.object({
  name: z.string().min(1, "Name is required").max(200),
  description: z.string(),
  working_directory: z.string(),
  repo_url: z.string(),
  default_branch: z.string().min(1, "Default branch is required").max(200),
});

type FormShape = z.infer<typeof formSchema>;

export interface ProjectFormValues {
  name: string;
  description: string;
  working_directory: string | null;
  repo_url: string | null;
  default_branch: string;
  env_vars: Record<string, string>;
  pipelines: Record<string, string>;
  auto_approve_nodes: string[];
}

interface ProjectFormProps {
  initialValues?: Partial<ProjectFormValues>;
  onSubmit: (values: ProjectFormValues) => Promise<unknown>;
  isPending: boolean;
  submitError?: unknown;
  submitLabel: string;
  cancelHref?: string;
}

export function ProjectForm({
  initialValues,
  onSubmit,
  isPending,
  submitError,
  submitLabel,
  cancelHref = "/projects",
}: ProjectFormProps) {
  const form = useForm<FormShape>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: initialValues?.name ?? "",
      description: initialValues?.description ?? "",
      working_directory: initialValues?.working_directory ?? "",
      repo_url: initialValues?.repo_url ?? "",
      default_branch: initialValues?.default_branch ?? "main",
    },
  });

  const [envVars, setEnvVars] = useState<Record<string, string>>(
    () => initialValues?.env_vars ?? {},
  );
  const [validationResults, setValidationResults] = useState<
    Record<string, EnvVarValidationResult>
  >({});
  const [validationWarning, setValidationWarning] = useState<string | null>(null);
  const currentUser = useCurrentUser();
  const isAdmin = currentUser.data?.is_superuser === true;
  const validateEnv = useValidateProjectEnv();

  const [pipelines, setPipelines] = useState<Record<string, string>>(
    () => initialValues?.pipelines ?? {},
  );
  const [autoApproveNodes, setAutoApproveNodes] = useState<string[]>(
    () => initialValues?.auto_approve_nodes ?? [],
  );

  const handleSubmit = form.handleSubmit(async (values) => {
    setValidationResults({});
    setValidationWarning(null);

    // Validate env vars before submitting.
    try {
      const res = await validateEnv.mutateAsync({ env_vars: envVars });
      const byKey: Record<string, EnvVarValidationResult> = {};
      let hasInvalid = false;
      for (const r of res.results) {
        byKey[r.key] = r;
        if (r.is_token && !r.valid) hasInvalid = true;
      }
      setValidationResults(byKey);
      if (hasInvalid) return; // Block save — inline feedback shown.
    } catch {
      // Validation itself failed (network error) — degrade to warning.
      setValidationWarning(
        "Could not validate GitHub tokens — saving anyway.",
      );
    }

    try {
      await onSubmit({
        name: values.name,
        description: values.description,
        // Empty strings collapse to null so the engine treats the
        // field as "not set" rather than persisting "".
        working_directory: values.working_directory.trim() || null,
        repo_url: values.repo_url.trim() || null,
        default_branch: values.default_branch,
        env_vars: envVars,
        // Drop incomplete bindings (blank kind or pipeline id) before submit
        // to avoid 422s from the engine validator.
        pipelines: Object.fromEntries(
          Object.entries(pipelines).filter(
            ([k, v]) => k.trim().length > 0 && v.trim().length > 0,
          ),
        ),
        auto_approve_nodes: autoApproveNodes,
      });
    } catch {
      // Parent surfaces submitError.
    }
  });

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <Field label="Name" error={form.formState.errors.name?.message}>
        <Input {...form.register("name")} placeholder="my-project" />
      </Field>

      <Field label="Description">
        <Textarea
          {...form.register("description")}
          rows={2}
          placeholder="What is this project for?"
        />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Working directory">
          <Input
            {...form.register("working_directory")}
            placeholder="/var/work/proj"
            className="font-mono text-xs"
          />
        </Field>

        <Field label="Default branch" error={form.formState.errors.default_branch?.message}>
          <Input
            {...form.register("default_branch")}
            placeholder="main"
            className="font-mono text-xs"
          />
        </Field>
      </div>

      <Field label="Repository URL">
        <Input
          {...form.register("repo_url")}
          placeholder="https://github.com/org/repo.git"
          className="font-mono text-xs"
        />
      </Field>

      <Field label="Project env vars">
        <EnvVarsEditor
          value={envVars}
          onChange={setEnvVars}
          validationResults={validationResults}
        />
        <p className="text-xs text-muted-foreground">
          Layered onto subprocess env (#65). Engine env (base) → these
          → per-agent runtime_config.env (highest). Keep secrets in
          engine env — these are convenience overrides.
        </p>
      </Field>

      <Field label="Pipeline bindings">
        <PipelineBindingsEditor value={pipelines} onChange={setPipelines} />
        <p className="text-xs text-muted-foreground">
          Map a workflow kind (e.g. &quot;cortex&quot;) to a pipeline. Bindings
          are optional — leave empty to configure later on the detail page.
        </p>
      </Field>

      {isAdmin ? (
        <Field label="Gate auto-approval">
          <GateAutoApproveEditor
            pipelines={pipelines}
            selectedNodes={autoApproveNodes}
            onChange={setAutoApproveNodes}
          />
          <p className="text-xs text-muted-foreground">
            Checked gates auto-resume for runs triggered through this project.
            Unchecked gates still pause for review.
          </p>
        </Field>
      ) : null}

      {validationWarning ? (
        <p className="text-sm text-yellow-600" role="status">
          {validationWarning}
        </p>
      ) : null}

      {submitError ? (
        <p className="text-sm text-destructive" role="alert">
          {formatApiError(submitError)}
        </p>
      ) : null}

      <div className="flex gap-2 pt-2">
        <Button type="submit" disabled={isPending || validateEnv.isPending}>
          {validateEnv.isPending ? "Validating…" : isPending ? "Saving…" : submitLabel}
        </Button>
        <Button type="button" variant="outline" asChild>
          <Link href={cancelHref}>Cancel</Link>
        </Button>
      </div>
    </form>
  );
}
