"use client";

import { useState } from "react";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Plus, Trash2 } from "lucide-react";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formatApiError } from "@/lib/api/client";
import { useCurrentUser, usePipelinesList, useValidateProjectEnv } from "@/hooks/api";
import type { EnvVarValidationResult, Pipeline } from "@/lib/api/types";
import {
  addBinding,
  updateBinding,
  removeBinding,
} from "@/components/projects/pipeline-bindings";

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

function toggleString(list: readonly string[], value: string, checked: boolean): string[] {
  const current = new Set(list);
  if (checked) {
    current.add(value);
  } else {
    current.delete(value);
  }
  return [...current];
}

interface GateAutoApproveEditorProps {
  pipelines: Record<string, string>;
  selectedNodes: string[];
  onChange: (next: string[]) => void;
}

function GateAutoApproveEditor({
  pipelines,
  selectedNodes,
  onChange,
}: GateAutoApproveEditorProps) {
  const { data: pipelinesList } = usePipelinesList();
  const pipelineById = new Map((pipelinesList?.items ?? []).map((pipeline) => [pipeline.id, pipeline]));
  const rows = buildGateAutoApproveRows(pipelines, selectedNodes, pipelineById);

  if (rows.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No approval gates found in the bound pipelines.
      </p>
    );
  }

  return (
    <div className="rounded-md border">
      <div className="grid grid-cols-[1fr_auto] gap-3 border-b px-3 py-2 text-xs font-medium text-muted-foreground">
        <span>Gate node</span>
        <span>Auto-approve</span>
      </div>
      <div className="divide-y">
        {rows.map((row) => (
          <label
            key={row.nodeId}
            className="grid grid-cols-[1fr_auto] items-center gap-3 px-3 py-2 text-sm"
          >
            <span>
              <span className="font-mono">{row.nodeId}</span>
              {row.pipelineNames.length > 0 ? (
                <span className="ml-2 text-xs text-muted-foreground">
                  {row.pipelineNames.join(", ")}
                </span>
              ) : (
                <span className="ml-2 text-xs text-yellow-600">
                  not in current bindings
                </span>
              )}
            </span>
            <input
              type="checkbox"
              className="h-4 w-4 accent-primary"
              checked={selectedNodes.includes(row.nodeId)}
              aria-describedby={`gate-auto-approve-${row.nodeId}-description`}
              onChange={(event) =>
                onChange(toggleString(selectedNodes, row.nodeId, event.target.checked))
              }
              aria-label={`Auto-approve ${row.nodeId}`}
            />
            <span id={`gate-auto-approve-${row.nodeId}-description`} className="sr-only">
              Auto-resume this approval gate for project-triggered runs.
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

interface GateAutoApproveRow {
  nodeId: string;
  pipelineNames: string[];
}

export function buildGateAutoApproveRows(
  bindings: Record<string, string>,
  selectedNodes: readonly string[],
  pipelineById: ReadonlyMap<string, Pick<Pipeline, "id" | "name" | "defaults">>,
): GateAutoApproveRow[] {
  const namesByNode = new Map<string, Set<string>>();
  for (const pipelineId of Object.values(bindings)) {
    const pipeline = pipelineById.get(pipelineId);
    if (!pipeline) continue;
    for (const nodeId of pipeline.defaults?.approval_required_nodes ?? []) {
      const names = namesByNode.get(nodeId) ?? new Set<string>();
      names.add(pipeline.name);
      namesByNode.set(nodeId, names);
    }
  }

  for (const nodeId of selectedNodes) {
    if (!namesByNode.has(nodeId)) {
      namesByNode.set(nodeId, new Set());
    }
  }

  return [...namesByNode.entries()]
    .map(([nodeId, pipelineNames]) => ({
      nodeId,
      pipelineNames: [...pipelineNames].sort((a, b) => a.localeCompare(b)),
    }))
    .sort((a, b) => a.nodeId.localeCompare(b.nodeId));
}

interface EnvVarsEditorProps {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  validationResults?: Record<string, EnvVarValidationResult>;
}

function EnvVarsEditor({ value, onChange, validationResults = {} }: EnvVarsEditorProps) {
  const entries = Object.entries(value);

  const update = (oldKey: string, nextKey: string, nextValue: string) => {
    const next: Record<string, string> = {};
    for (const [k, v] of Object.entries(value)) {
      if (k === oldKey) {
        if (nextKey.length > 0) next[nextKey] = nextValue;
      } else {
        next[k] = v;
      }
    }
    onChange(next);
  };

  const remove = (key: string) => {
    const { [key]: _removed, ...rest } = value;
    onChange(rest);
  };

  const add = () => {
    let candidate = "VAR_NAME";
    let n = 1;
    while (candidate in value) {
      candidate = `VAR_NAME_${n++}`;
    }
    onChange({ ...value, [candidate]: "" });
  };

  return (
    <div className="space-y-2">
      {entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No project env vars yet.
        </p>
      ) : (
        <div className="space-y-1">
          {entries.map(([key, val]) => {
            const vr = validationResults[key];
            const hasError = vr?.is_token && !vr.valid;
            return (
              <div key={key}>
                <div className="flex items-center gap-2">
                  <Input
                    defaultValue={key}
                    onBlur={(e) => update(key, e.target.value, val)}
                    placeholder="KEY"
                    className="font-mono text-xs h-8 max-w-[14rem]"
                    aria-label="env var name"
                  />
                  <Input
                    value={val}
                    onChange={(e) => update(key, key, e.target.value)}
                    placeholder="value"
                    className="font-mono text-xs h-8"
                    aria-label="env var value"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(key)}
                    aria-label={`Remove ${key}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {hasError && (
                  <p className="text-xs text-destructive ml-1 mt-0.5">
                    {vr.error ?? "Invalid token"}
                  </p>
                )}
                {vr?.is_token && vr.valid && vr.login && (
                  <p className="text-xs text-green-600 ml-1 mt-0.5">
                    Authenticated as {vr.login}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
      <Button type="button" variant="outline" size="sm" onClick={add}>
        <Plus className="h-3.5 w-3.5 mr-1" />
        Add env var
      </Button>
    </div>
  );
}

interface PipelineBindingsEditorProps {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}

function PipelineBindingsEditor({ value, onChange }: PipelineBindingsEditorProps) {
  const { data: pipelinesList } = usePipelinesList();
  const entries = Object.entries(value);

  return (
    <div className="space-y-2">
      {entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No pipeline bindings yet.
        </p>
      ) : (
        <div className="space-y-1">
          {entries.map(([kind, pipelineId]) => (
            <div key={kind} className="flex items-center gap-2">
              <Input
                defaultValue={kind}
                onBlur={(e) =>
                  onChange(updateBinding(value, kind, e.target.value.trim(), pipelineId))
                }
                placeholder="kind"
                className="font-mono text-xs h-8 max-w-[14rem]"
                aria-label="binding kind"
              />
              <select
                value={pipelineId}
                onChange={(e) =>
                  onChange(updateBinding(value, kind, kind, e.target.value))
                }
                className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-xs font-mono shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                aria-label="pipeline"
              >
                <option value="">Select pipeline…</option>
                {pipelinesList?.items.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onChange(removeBinding(value, kind))}
                aria-label={`Remove ${kind}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange(addBinding(value))}
      >
        <Plus className="h-3.5 w-3.5 mr-1" />
        Add binding
      </Button>
    </div>
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
