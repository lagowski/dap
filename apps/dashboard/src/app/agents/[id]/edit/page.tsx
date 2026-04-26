"use client";

import { use } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useAgent, useUpdateAgent } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AgentForm } from "@/components/agents/agent-form";

export default function EditAgentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: agent, isPending, isError, error } = useAgent(id);
  const update = useUpdateAgent();

  if (isPending) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }
  if (isError) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {formatApiError(error)}
          </CardContent>
        </Card>
      </div>
    );
  }

  const nextVersion = agent.version + 1;

  return (
    <div className="p-6 space-y-4 max-w-3xl">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/agents/${id}`} aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold">Edit {agent.name}</h1>
        <span className="text-sm text-muted-foreground">
          (v{agent.version} → saving will create v{nextVersion})
        </span>
      </div>

      <Card>
        <CardContent className="pt-6">
          <AgentForm
            initialValues={{
              name: agent.name,
              role: agent.role,
              runtime_id: agent.runtime_id,
              prompt_template: agent.prompt_template,
            }}
            // Role is immutable on the server: a different role would be a
            // different agent. Lock it to avoid silent rejection.
            lockedFields={["role"]}
            onSubmit={async (values) => {
              // role is locked on the form, but the engine ignores it on
              // PUT — strip from payload anyway for shape clarity.
              const { role: _role, ...rest } = values;
              await update.mutateAsync({
                id,
                payload: {
                  ...rest,
                  runtime_config: agent.runtime_config,
                  input_schema: agent.input_schema,
                  output_schema: agent.output_schema,
                  constraints: agent.constraints,
                  budget_limit_usd: agent.budget_limit_usd,
                  timeout_ms: agent.timeout_ms,
                },
              });
              router.push(`/agents/${id}`);
            }}
            isPending={update.isPending}
            submitError={update.error}
            submitLabel={`Save v${nextVersion}`}
            cancelHref={`/agents/${id}`}
          />
        </CardContent>
      </Card>
    </div>
  );
}
