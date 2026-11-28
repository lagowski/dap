"use client";

import { use, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, ArrowLeft, ChevronDown, ChevronRight, Copy, Download, Pencil } from "lucide-react";
import {
  useAgent,
  useAgentCallableInfo,
  useAgentExecutions,
  useAgentUsage,
  useAgentVersions,
  useArchiveAgent,
} from "@/hooks/api";
import { downloadAgentExportWithAlert } from "@/lib/agent-export";
import { formatApiError } from "@/lib/api/client";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { NodeStatusBadge } from "@/components/status-badge";
import { PromptTemplateCard } from "@/components/agents/agent-prompt-template-card";
import { ManagedAgentBanner } from "@/components/agents/managed-agent-banner";
import { CallableDoc } from "@/components/agents/callable-doc";
import { isManagedAgent } from "@/lib/managed-agent";
import { useConfirmDestructive } from "@/components/confirm-destructive-dialog";
import type { Agent, NodeStatus } from "@/lib/api/types";

export default function AgentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: agent, isPending, isError, error } = useAgent(id);
  const versions = useAgentVersions(id);
  const archive = useArchiveAgent();
  const confirmDestructive = useConfirmDestructive();
  // "What this agent does" (#747) — only fetched for managed agents, where the
  // callable's docstring is the real description (the prompt is inert).
  const callableInfo = useAgentCallableInfo(id, {
    enabled: agent != null && isManagedAgent(agent),
  });

  if (isPending) {
    return <LoadingState className="p-6" />;
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

  const handleArchive = async () => {
    const ok = await confirmDestructive({
      title: "Archive agent",
      description: `Archive agent "${agent.name}"? Existing pipelines that reference it will keep working, but it won't appear in pickers.`,
      confirmLabel: "Archive",
    });
    if (!ok) {
      return;
    }
    archive.mutate(id, {
      onSuccess: () => router.push("/agents"),
    });
  };

  const handleExport = () => downloadAgentExportWithAlert(agent);

  return (
    <div className="p-6 space-y-4 max-w-4xl">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/agents" aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-semibold">{agent.name}</h1>
        <Badge variant="secondary">{agent.role}</Badge>
        <Badge variant="outline">v{agent.version}</Badge>
        {!agent.is_active && <Badge variant="destructive">archived</Badge>}
        <div className="ml-auto flex items-center gap-2">
          {agent.is_active && (
            <>
              <Button asChild size="sm">
                <Link href={`/agents/${agent.id}/edit`}>
                  <Pencil className="h-3.5 w-3.5 mr-1" />
                  Edit
                </Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link href={`/agents/new?from=${agent.id}`}>
                  <Copy className="h-3.5 w-3.5 mr-1" />
                  Clone
                </Link>
              </Button>
              <Button variant="outline" size="sm" onClick={handleExport}>
                <Download className="h-3.5 w-3.5 mr-1" />
                Export JSON
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={archive.isPending}
                onClick={handleArchive}
              >
                <Archive className="h-3.5 w-3.5 mr-1" />
                {archive.isPending ? "Archiving…" : "Archive"}
              </Button>
            </>
          )}
        </div>
      </div>

      {archive.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {formatApiError(archive.error)}
        </p>
      ) : null}

      <div className="grid grid-cols-4 gap-4 text-xs">
        <Metric label="Runtime" value={agent.runtime_id} mono />
        <Metric
          label="Budget (USD)"
          value={agent.budget_limit_usd != null ? `$${agent.budget_limit_usd}` : "—"}
        />
        <Metric label="Timeout (ms)" value={String(agent.timeout_ms)} />
        <Metric label="ID" value={agent.id.slice(0, 8) + "…"} mono />
      </div>

      <ManagedAgentBanner agent={agent} />

      {isManagedAgent(agent) && <CallableDoc info={callableInfo.data} />}

      <PromptTemplateCard
        runtimeId={agent.runtime_id}
        promptTemplate={agent.prompt_template}
        runtimeConfig={agent.runtime_config}
        version={agent.version}
      />

      {Object.keys(agent.runtime_config).length > 0 && (
        <Card>
          <CardContent className="pt-6 space-y-2">
            <h2 className="text-sm font-medium">Runtime config</h2>
            <pre className="rounded border bg-muted/30 p-3 text-xs overflow-x-auto">
              {JSON.stringify(agent.runtime_config, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="pt-6 space-y-3">
          <h2 className="text-sm font-medium">Contracts</h2>
          <FieldList label="Inputs" fields={agent.input_schema} legacyHint="legacy mode — template sees full PipelineState" />
          <FieldList
            label="Outputs"
            fields={agent.output_schema}
            legacyHint="legacy mode — engine falls back to ROLE_FIELDS for known roles"
          />
        </CardContent>
      </Card>

      <UsedInCard agentId={id} />

      <ActivityCard agentId={id} />

      <VersionHistory
        currentVersion={agent.version}
        versions={versions.data}
        isPending={versions.isPending}
        isError={versions.isError}
        error={versions.error}
      />
    </div>
  );
}

function UsedInCard({ agentId }: { agentId: string }) {
  const usage = useAgentUsage(agentId);
  const data = usage.data;
  const hasAny =
    data != null && (data.pipelines.length > 0 || data.projects.length > 0);

  return (
    <Card>
      <CardContent className="pt-6 space-y-3">
        <h2 className="text-sm font-medium">Used in</h2>
        {usage.isPending ? (
          <LoadingState />
        ) : usage.isError ? (
          <p className="text-sm text-destructive">
            {formatApiError(usage.error)}
          </p>
        ) : hasAny ? (
          <div className="space-y-4">
            <div>
              <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Pipelines ({data.pipelines.length})
              </h3>
              {data.pipelines.length > 0 ? (
                <ul className="space-y-1">
                  {data.pipelines.map((p) => (
                    <li key={p.id}>
                      <Link
                        href={`/pipelines/${p.id}/edit`}
                        className="text-sm hover:underline"
                      >
                        {p.name}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">—</p>
              )}
            </div>
            <div>
              <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Projects ({data.projects.length})
              </h3>
              {data.projects.length > 0 ? (
                <ul className="space-y-1.5">
                  {data.projects.map((proj) => (
                    <li
                      key={proj.id}
                      className="flex flex-wrap items-center gap-2"
                    >
                      <Link
                        href={`/projects/${proj.id}`}
                        className="text-sm hover:underline"
                      >
                        {proj.name}
                      </Link>
                      {proj.bindings.map((b) => (
                        <Badge
                          key={`${b.kind}:${b.pipeline_id}`}
                          variant="secondary"
                          className="font-mono text-[10px]"
                          title={`bound as ${b.kind}`}
                        >
                          {b.kind}
                        </Badge>
                      ))}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Not bound into any project.
                </p>
              )}
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Not used in any pipeline yet — safe to edit or archive.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ActivityCard({ agentId }: { agentId: string }) {
  const exec = useAgentExecutions(agentId);
  const data = exec.data;

  return (
    <Card>
      <CardContent className="pt-6 space-y-3">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-sm font-medium">Activity</h2>
          {data && data.total > 0 ? (
            <span className="text-xs text-muted-foreground">
              {data.items.length >= data.total
                ? `${data.total} execution${data.total === 1 ? "" : "s"}`
                : `latest ${data.items.length} of ${data.total}`}
            </span>
          ) : null}
        </div>
        {exec.isPending ? (
          <LoadingState />
        ) : exec.isError ? (
          <p className="text-sm text-destructive">
            {formatApiError(exec.error)}
          </p>
        ) : data && data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b text-left text-muted-foreground">
                <tr>
                  <th className="px-3 py-1.5 font-medium">Run</th>
                  <th className="px-3 py-1.5 font-medium">Node</th>
                  <th className="px-3 py-1.5 font-medium">Status</th>
                  <th className="px-3 py-1.5 font-medium">Started</th>
                  <th className="px-3 py-1.5 font-medium text-right">Duration</th>
                  <th className="px-3 py-1.5 font-medium text-right">Tokens</th>
                  <th className="px-3 py-1.5 font-medium text-right">Cost</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((e) => (
                  <tr
                    key={e.id}
                    className="border-b last:border-0 hover:bg-muted/30"
                    title={e.error_message ?? undefined}
                  >
                    <td className="p-0 font-mono text-xs">
                      <Link
                        href={`/runs/${e.run_id}`}
                        className="block px-3 py-1.5 hover:underline"
                      >
                        {e.run_id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td className="px-3 py-1.5 font-mono text-xs">{e.node_id}</td>
                    <td className="px-3 py-1.5">
                      <NodeStatusBadge status={e.status as NodeStatus} />
                    </td>
                    <td
                      className="whitespace-nowrap px-3 py-1.5 text-xs text-muted-foreground"
                      suppressHydrationWarning
                    >
                      {new Date(e.started_at).toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {formatDuration(e.duration_ms)}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {formatTokens(e.tokens_used)}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {formatCost(e.cost_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No executions yet — this agent hasn&apos;t run in any pipeline.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function VersionHistory({
  currentVersion,
  versions,
  isPending,
  isError,
  error,
}: {
  currentVersion: number;
  versions: Agent[] | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
}) {
  if (isPending) {
    return (
      <Card>
        <CardContent className="pt-6 text-sm text-muted-foreground">
          Loading version history…
        </CardContent>
      </Card>
    );
  }
  if (isError) {
    return (
      <Card className="border-destructive/50">
        <CardContent className="pt-6 text-sm text-destructive">
          Could not load version history: {formatApiError(error)}
        </CardContent>
      </Card>
    );
  }

  // Engine returns versions ascending; show newest first.
  const sorted = [...(versions ?? [])].sort((a, b) => b.version - a.version);
  const previous = sorted.filter((v) => v.version !== currentVersion);

  if (previous.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6 text-sm text-muted-foreground">
          No previous versions yet — this is v1.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="pt-6 space-y-3">
        <h2 className="text-sm font-medium">Version history</h2>
        <ul className="space-y-2">
          {previous.map((v) => (
            <VersionRow key={v.version} version={v} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function VersionRow({ version }: { version: Agent }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <li className="rounded border">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-muted/30"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        )}
        <Badge variant="outline">v{version.version}</Badge>
        <span className="font-medium">{version.name}</span>
        <span className="text-muted-foreground ml-auto">
          <span suppressHydrationWarning><span suppressHydrationWarning>{new Date(version.created_at).toLocaleString()}</span></span>
        </span>
      </button>
      {expanded && (
        <div className="border-t px-3 py-2 space-y-2 text-xs">
          <Detail label="Runtime" value={version.runtime_id} mono />
          {Object.keys(version.runtime_config).length > 0 && (
            <Detail
              label="Runtime config"
              value={JSON.stringify(version.runtime_config, null, 2)}
              block
            />
          )}
          <FieldList label="Inputs" fields={version.input_schema} legacyHint="—" />
          <FieldList label="Outputs" fields={version.output_schema} legacyHint="—" />
          <Detail label="Prompt template" value={version.prompt_template} block />
        </div>
      )}
    </li>
  );
}

function FieldList({
  label,
  fields,
  legacyHint,
}: {
  label: string;
  fields: readonly string[];
  legacyHint: string;
}) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      {fields.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">{legacyHint}</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {fields.map((f) => (
            <Badge key={f} variant="outline" className="font-mono text-xs">
              {f}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded border bg-background p-3">
      <div className="text-muted-foreground">{label}</div>
      <div className={`font-medium ${mono ? "font-mono" : ""}`}>{value}</div>
    </div>
  );
}

function Detail({
  label,
  value,
  mono,
  block,
}: {
  label: string;
  value: string;
  mono?: boolean;
  block?: boolean;
}) {
  return (
    <div className="space-y-1">
      <div className="text-muted-foreground">{label}</div>
      {block ? (
        <pre
          className={`rounded border bg-muted/30 p-2 overflow-x-auto whitespace-pre-wrap ${
            mono ? "font-mono" : ""
          }`}
        >
          {value}
        </pre>
      ) : (
        <div className={mono ? "font-mono" : ""}>{value}</div>
      )}
    </div>
  );
}
