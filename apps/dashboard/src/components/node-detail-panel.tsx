"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import {
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FlaskConical,
  Loader2,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAssistantPrefill } from "@/components/assistant/assistant-prefill";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import {
  useAgentsList,
  useRunNodeExplain,
  useRunNodeLog,
  useRunStateHistory,
} from "@/hooks/api";
import { NodeStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { StateDiffView } from "@/components/state-diff-view";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";
import type { PipelineState, StateSnapshot } from "@/lib/api/types";

interface NodeDetailPanelProps {
  runId: string;
  nodeId: string | null;
  /** Execution-ordered node ids — enables prev/next stepping in-panel. */
  nodeIds?: string[];
  onOpenChange: (open: boolean) => void;
  /** Switch the panel to another node without closing it. */
  onSelectNode?: (nodeId: string) => void;
}

export function NodeDetailPanel({
  runId,
  nodeId,
  nodeIds,
  onOpenChange,
  onSelectNode,
}: NodeDetailPanelProps) {
  const open = nodeId != null;
  const { data, isPending, isError, error } = useRunNodeLog(runId, nodeId);
  const { data: stateHistory } = useRunStateHistory(open ? runId : null);
  // Resolve the agent so we can show its name/role + a Cortex tag instead of a
  // bare UUID (#724). Shared, cached query — no per-node fetch.
  const { data: agentsList } = useAgentsList();
  const agent = data
    ? (agentsList?.items ?? []).find((a) => a.id === data.agent_id)
    : undefined;
  const callablePath =
    typeof agent?.runtime_config?.callable_path === "string"
      ? agent.runtime_config.callable_path
      : null;
  const isCortex =
    data?.runtime_id === "python-func" && callablePath?.startsWith("cortex.") === true;
  const router = useRouter();
  const { setPrefill } = useAssistantPrefill();
  // Cortex python-func nodes build their own prompt inside the callable and
  // record it into the node output's ``extensions.__audit`` (#724). Surface it.
  const recordedPrompt = recordedPromptFrom(data?.output_json ?? null);
  // Show the Prompt tab for LLM/CLI runtimes (they always have a prompt concept,
  // empty → placeholder) and for cortex python-func nodes that recorded a
  // prompt. Plain python-func with no recorded prompt has none (#705).
  const showPromptTab = data?.runtime_id !== "python-func" || recordedPrompt != null;

  const { before, after } = useMemo(() => {
    return getSnapshotPair(stateHistory ?? [], nodeId);
  }, [stateHistory, nodeId]);

  // Step through nodes in execution order without closing the dialog.
  const order = nodeIds ?? [];
  const idx = nodeId ? order.indexOf(nodeId) : -1;
  const prevId = idx > 0 ? order[idx - 1] : null;
  const nextId = idx >= 0 && idx < order.length - 1 ? order[idx + 1] : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <div className="flex items-center justify-between gap-2 pr-8">
            <DialogTitle className="flex min-w-0 items-center gap-2">
              Node: <span className="font-mono truncate">{nodeId}</span>
              {data && <NodeStatusBadge status={data.status} />}
            </DialogTitle>
            {order.length > 1 && idx >= 0 ? (
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7"
                  disabled={!prevId}
                  onClick={() => prevId && onSelectNode?.(prevId)}
                  aria-label="Previous node"
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {idx + 1} / {order.length}
                </span>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7"
                  disabled={!nextId}
                  onClick={() => nextId && onSelectNode?.(nextId)}
                  aria-label="Next node"
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            ) : null}
          </div>
        </DialogHeader>

        {isPending && nodeId && (
          <LoadingState />
        )}

        {isError &&
          (error instanceof ApiError && error.status === 404 ? (
            // The node exists in the pipeline graph but never executed in this
            // run (e.g. the run paused/ended at an earlier gate). There's no
            // execution log to show — say so plainly instead of a scary error.
            <p className="text-sm text-muted-foreground">
              This node didn’t run in this run — no execution log to show.
            </p>
          ) : (
            <p className="text-sm text-destructive">
              Failed to load: {(error as Error).message}
            </p>
          ))}

        {data && (
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-3 gap-4 text-xs">
              <Metric label="Duration" value={formatDuration(data.duration_ms)} />
              <Metric label="Tokens" value={formatTokens(data.tokens_used)} />
              <Metric label="Cost" value={formatCost(data.cost_usd)} />
            </div>

            {data.error_message && (
              <Section title="Error">
                <pre className="text-xs bg-destructive/10 text-destructive p-3 rounded whitespace-pre-wrap">
                  {data.error_message}
                </pre>
                {nodeId && <ErrorExplainer runId={runId} nodeId={nodeId} />}
              </Section>
            )}

            {/* Structured (parsed output_json) and raw stdout are separate
                tabs — for many nodes stdout *is* the JSON, so showing both
                stacked in one "Output" tab looked like a duplicate. Default
                to the readable structured view when it exists. */}
            <Tabs
              defaultValue={data.output_json != null ? "structured" : "stdout"}
            >
              <TabsList className="w-full">
                {data.output_json != null && (
                  <TabsTrigger value="structured" className="flex-1">
                    Structured
                  </TabsTrigger>
                )}
                <TabsTrigger value="stdout" className="flex-1">
                  Std output
                </TabsTrigger>
                {/* Show the Prompt tab when there's a prompt to show: the
                    rendered prompt_xml for LLM/CLI nodes, or — for cortex
                    python-func nodes — the prompt the callable recorded into
                    extensions.__audit (#724). Plain python-func with neither
                    has no prompt, so the tab stays hidden (#705). */}
                {showPromptTab && (
                  <TabsTrigger value="prompt" className="flex-1">
                    Prompt
                  </TabsTrigger>
                )}
                <TabsTrigger value="state-diff" className="flex-1">
                  State diff
                </TabsTrigger>
              </TabsList>

              {data.output_json != null && (
                <TabsContent value="structured">
                  <pre className="text-xs bg-muted p-3 rounded overflow-x-auto">
                    {JSON.stringify(data.output_json, null, 2)}
                  </pre>
                </TabsContent>
              )}

              <TabsContent value="stdout">
                {data.stdout ? (
                  <pre className="text-xs bg-muted p-3 rounded overflow-x-auto whitespace-pre-wrap">
                    {data.stdout}
                  </pre>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    No stdout output.
                  </p>
                )}
              </TabsContent>

              {showPromptTab && (
                <TabsContent value="prompt" className="space-y-3">
                  {recordedPrompt ? (
                    <>
                      <p className="text-[11px] text-muted-foreground">
                        Prompt as sent by the callable (recorded in{" "}
                        <span className="font-mono">extensions.__audit</span>).
                      </p>
                      {recordedPrompt.system && (
                        <PromptSection title="System" body={recordedPrompt.system} />
                      )}
                      {recordedPrompt.user && (
                        <PromptSection title="User" body={recordedPrompt.user} />
                      )}
                    </>
                  ) : data.prompt_xml ? (
                    <pre className="text-xs bg-muted p-3 rounded overflow-x-auto whitespace-pre-wrap">
                      {data.prompt_xml}
                    </pre>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      No prompt recorded for this node.
                    </p>
                  )}
                </TabsContent>
              )}

              <TabsContent value="state-diff">
                {after ? (
                  // StateDiffView renders its own "No state fields changed"
                  // empty state when the node ran but mutated nothing — which
                  // is distinct from the "no snapshot" case below.
                  <StateDiffView before={before} after={after} />
                ) : (
                  <p className="text-xs text-muted-foreground">
                    No state snapshot was recorded for this node.
                  </p>
                )}
              </TabsContent>
            </Tabs>

            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {agent ? (
                <Link
                  href={`/agents/${agent.id}`}
                  aria-label={`View agent ${agent.name}`}
                  className="font-medium text-foreground hover:underline"
                >
                  {agent.name}
                </Link>
              ) : (
                <span className="font-mono">agent {data.agent_id.slice(0, 8)}…</span>
              )}
              {agent && <Badge variant="secondary">{agent.role}</Badge>}
              <span className="font-mono">{data.runtime_id}</span>
              {isCortex && <Badge variant="outline">Cortex</Badge>}
            </div>

            {agent && (
              // Open the agent's tester pre-seeded with the state this node
              // received, so you can tweak params and re-run its work (#724).
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  const inputState = before ?? after;
                  if (inputState) {
                    setPrefill({
                      target: "agent-test",
                      values: { context: JSON.stringify(inputState, null, 2) },
                    });
                  }
                  router.push(`/agents/${agent.id}/edit`);
                  onOpenChange(false);
                }}
              >
                <FlaskConical className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                Open in agent tester
              </Button>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * "Explain this error" affordance for a failed node (#691). On click it
 * fetches the deterministic explanation (plain-language cause + suggested
 * actions). Suggested actions are advisory — the UI never auto-applies them.
 */
function ErrorExplainer({ runId, nodeId }: { runId: string; nodeId: string }) {
  const [open, setOpen] = useState(false);
  const [ai, setAi] = useState(false);
  const { data, isLoading, isError } = useRunNodeExplain(runId, nodeId, open, ai);
  // a11y: when the user clicks "Ask AI to explain" that button unmounts as the
  // LLM answer arrives, dropping keyboard focus to <body>. Move focus to the
  // explanation text so screen-reader / keyboard users land on the new content.
  const causeRef = useRef<HTMLParagraphElement>(null);
  const focusPending = useRef(false);
  useEffect(() => {
    if (focusPending.current && data?.source === "llm") {
      causeRef.current?.focus();
      focusPending.current = false;
    }
  }, [data?.source]);

  if (!open) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="mt-2"
        onClick={() => setOpen(true)}
      >
        <Sparkles className="mr-1 h-3.5 w-3.5" aria-hidden />
        Explain this error
      </Button>
    );
  }

  return (
    <div className="mt-2 rounded border bg-muted/40 p-3 text-xs space-y-2">
      {isLoading && (
        <span className="flex items-center gap-1.5 text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Analyzing…
        </span>
      )}
      {isError && (
        <span className="text-destructive">Could not analyze this error.</span>
      )}
      {data && (
        <>
          {data.source === "llm" && (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium uppercase text-blue-600 dark:text-blue-400">
              <Sparkles className="h-3 w-3" aria-hidden />
              AI explanation
            </span>
          )}
          <p
            ref={causeRef}
            tabIndex={-1}
            className="whitespace-pre-wrap font-medium text-foreground rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {data.cause}
          </p>
          {data.actions.length > 0 && (
            <ul className="list-disc space-y-1 pl-4">
              {data.actions.map((a, i) => (
                <li key={i}>{a.text}</li>
              ))}
            </ul>
          )}
          {data.docs.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-1">
              {data.docs.map((d) => (
                <Link
                  key={d.href}
                  href={d.href}
                  target="_blank"
                  className="inline-flex items-center gap-1 text-blue-600 hover:underline dark:text-blue-400"
                >
                  {d.label}
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </Link>
              ))}
            </div>
          )}
          {!data.recognized && data.source !== "llm" && (
            // Deterministic didn't recognise it — offer the LLM fallback (#691
            // slice 2). On-demand so we don't spend a model call unasked.
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="h-7"
              onClick={() => {
                focusPending.current = true;
                setAi(true);
              }}
            >
              <Sparkles className="mr-1 h-3.5 w-3.5" aria-hidden />
              Ask AI to explain
            </Button>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Pull the prompt a cortex python-func callable recorded into the node output
 * (#724): ``output_json.state_delta.extensions.__audit.{system_prompt,
 * user_prompt}``. Returns null when nothing is recorded. Defensive — the shape
 * is free-form JSON.
 */
function recordedPromptFrom(
  output: Record<string, unknown> | null,
): { system?: string; user?: string } | null {
  if (!output || typeof output !== "object") return null;
  const stateDelta = (output as Record<string, unknown>).state_delta;
  const extensions =
    stateDelta && typeof stateDelta === "object"
      ? (stateDelta as Record<string, unknown>).extensions
      : null;
  const audit =
    extensions && typeof extensions === "object"
      ? (extensions as Record<string, unknown>).__audit
      : null;
  if (!audit || typeof audit !== "object") return null;
  const a = audit as Record<string, unknown>;
  const system = typeof a.system_prompt === "string" ? a.system_prompt : undefined;
  const user = typeof a.user_prompt === "string" ? a.user_prompt : undefined;
  if (!system && !user) return null;
  return { system, user };
}

function PromptSection({ title, body }: { title: string; body: string }) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-medium uppercase text-muted-foreground">
        {title}
      </div>
      <pre className="max-h-[40vh] overflow-auto rounded bg-muted p-3 text-xs whitespace-pre-wrap">
        {body}
      </pre>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border p-2">
      <div className="text-muted-foreground">{label}</div>
      <div className="font-medium tabular-nums">{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-xs font-medium uppercase text-muted-foreground mb-1">
        {title}
      </h4>
      {children}
    </div>
  );
}

function getSnapshotPair(
  snapshots: StateSnapshot[],
  nodeId: string | null,
): { before: PipelineState | null; after: PipelineState | null } {
  if (!nodeId || snapshots.length === 0) {
    return { before: null, after: null };
  }

  const idx = snapshots.findIndex((s) => s.node_id === nodeId);
  if (idx === -1) {
    return { before: null, after: null };
  }

  const after = snapshots[idx].state;
  const before = idx > 0 ? snapshots[idx - 1].state : null;
  return { before, after };
}
