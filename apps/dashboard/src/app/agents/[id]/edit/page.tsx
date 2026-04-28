"use client";

import { useId, useState, use } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useAgent, useUpdateAgent } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AgentForm, type AgentFormValues } from "@/components/agents/agent-form";
import { AgentTabs, type AgentTab } from "@/components/agents/agent-tabs";
import { AgentTestPanel } from "@/components/agents/agent-test-panel";
import type { Agent, AgentDryRunDraft } from "@/lib/api/types";

export default function EditAgentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: agent, isPending, isError, error } = useAgent(id);
  const update = useUpdateAgent();
  const [tab, setTab] = useState<AgentTab>("form");
  const [snapshot, setSnapshot] = useState<{
    values: AgentFormValues;
    valid: boolean;
  } | null>(null);
  const formPanelId = useId();
  const testPanelId = useId();

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
  const draft = snapshot && snapshot.valid ? toDryRunDraft(snapshot.values, agent) : null;
  const draftBlockedReason =
    snapshot === null
      ? "Open the Form tab to fill in the agent first."
      : !snapshot.valid
        ? "Fix form errors before running a test."
        : null;

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

      <AgentTabs
        tab={tab}
        onTabChange={setTab}
        formPanelId={formPanelId}
        testPanelId={testPanelId}
      />

      <Card>
        <CardContent className="pt-6">
          {/* Both panels stay mounted so the form's onValuesChange keeps
              firing while the user is on the Test tab — switching back
              shouldn't reset their inputs. We just hide the inactive one. */}
          <div
            id={formPanelId}
            role="tabpanel"
            hidden={tab !== "form"}
            className={tab === "form" ? "block" : "hidden"}
          >
            <AgentForm
              initialValues={{
                name: agent.name,
                role: agent.role,
                runtime_id: agent.runtime_id,
                runtime_config: agent.runtime_config,
                prompt_template: agent.prompt_template,
                input_schema: agent.input_schema,
                output_schema: agent.output_schema,
              }}
              lockedFields={["role"]}
              onValuesChange={setSnapshot}
              onSubmit={async (values) => {
                await update.mutateAsync({
                  id,
                  payload: {
                    name: values.name,
                    runtime_id: values.runtime_id,
                    runtime_config: values.runtime_config,
                    prompt_template: values.prompt_template,
                    input_schema: values.input_schema,
                    output_schema: values.output_schema,
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
          </div>

          <div
            id={testPanelId}
            role="tabpanel"
            hidden={tab !== "test"}
            className={tab === "test" ? "block" : "hidden"}
          >
            <AgentTestPanel
              draft={draft}
              draftBlockedReason={draftBlockedReason}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Build the dry-run draft from the live form values + the persisted
 * agent's non-editable fields (constraints, budget, timeout). The
 * Edit form doesn't surface those; reuse what's on the server so the
 * test reflects what would actually run after Save.
 */
function toDryRunDraft(values: AgentFormValues, agent: Agent): AgentDryRunDraft {
  return {
    name: values.name,
    role: values.role,
    runtime_id: values.runtime_id,
    runtime_config: values.runtime_config,
    prompt_template: values.prompt_template,
    input_schema: values.input_schema,
    output_schema: values.output_schema,
    constraints: agent.constraints,
    budget_limit_usd: agent.budget_limit_usd,
    timeout_ms: agent.timeout_ms,
  };
}
