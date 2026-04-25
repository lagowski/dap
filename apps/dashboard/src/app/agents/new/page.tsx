"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ArrowLeft } from "lucide-react";
import { useCreateAgent } from "@/hooks/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const RUNTIMES = ["api-call", "claude-code", "gemini-cli", "codex", "aider", "bash", "http"] as const;

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

type FormValues = z.infer<typeof formSchema>;

const DEFAULT_TEMPLATE = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <task>
    Describe the task for this agent.
  </task>
</agent_prompt>`;

export default function NewAgentPage() {
  const router = useRouter();
  const create = useCreateAgent();

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
      role: ROLES[0],
      runtime_id: "api-call",
      prompt_template: DEFAULT_TEMPLATE,
    },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    const agent = await create.mutateAsync(values);
    router.push(`/agents`);
    return agent;
  });

  return (
    <div className="p-6 space-y-4 max-w-3xl">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/agents" aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold">New agent</h1>
      </div>

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={onSubmit} className="space-y-4">
            <Field label="Name" error={form.formState.errors.name?.message}>
              <Input {...form.register("name")} placeholder="Test Author" />
            </Field>

            <Field label="Role" error={form.formState.errors.role?.message}>
              <select
                {...form.register("role")}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
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
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
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

            {create.isError && (
              <p className="text-sm text-destructive">
                {(create.error as Error).message}
              </p>
            )}

            <div className="flex gap-2 pt-2">
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? "Creating…" : "Create agent"}
              </Button>
              <Button type="button" variant="outline" asChild>
                <Link href="/agents">Cancel</Link>
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
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
