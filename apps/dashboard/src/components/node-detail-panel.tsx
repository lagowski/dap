"use client";

import { useMemo, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import { ChevronLeft, ChevronRight, ExternalLink, Loader2, Sparkles, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { useAgent, useRunNodeExplain, useRunNodeLog, useRunStateHistory } from "@/hooks/api";
import { NodeStatusBadge } from "@/components/status-badge";
import { StateDiffView } from "@/components/state-diff-view";
import { useAssistantPrefill } from "@/components/assistant/assistant-prefill";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";
import type { PipelineState, StateSnapshot } from "@/lib/api/types";

/**
 * #724 reserved structured-output key that lets a python-func callable
 * record the LLM prompt it built internally. DAP renders it in the Prompt
 * tab when present. Populating it is a dap-cortex change (filed
 * separately) — DAP just renders whatever lands.
 */
const RECORDED_PROMPT_KEY = "__prompt";

function recordedPromptFrom(outputJson: unknown): string | null {
  if (outputJson == null || typeof outputJson !== "object") return null;
  const candidate = (outputJson as Record<string, unknown>)[RECORDED_PROMPT_KEY];
  return typeof candidate === "string" && candidate.length > 0 ? candidate : null;
}

/**
 * #724 helper — a Cortex callable is a python-func agent whose runtime_config
 * carries a ``callable_path`` starting with ``cortex.``. The badge tells the
 * operator at a glance that this node is bound to the cortex pipeline source,
 * not generic python.
 */
function isCortexCallable(runtimeConfig: unknown): boolean {
  if (runtimeConfig == null || typeof runtimeConfig !== "object") return false;
  const cp = (runtimeConfig as Record<string, unknown>).callable_path;
  return typeof cp === "string" && cp.startsWith("cortex.");
}

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
  // #724 — resolve agent_id → name/role/runtime_config for the footer chip,
  // the Cortex tag, and the "Open in agent tester" action. Skipped when
  // the dialog is closed or the node hasn't loaded yet.
  const agentId = data?.agent_id ?? null;
  const { data: agent } = useAgent(agentId);
  const router = useRouter();
  const { setPrefill } = useAssistantPrefill();

  const { before, after } = useMemo(() => {
    return getSnapshotPair(stateHistory ?? [], nodeId);
  }, [stateHistory, nodeId]);

  /**
   * #724 — stash the node's runtime_config + input state and navigate to
   * the agent's Edit page, where the Test panel consumes the prefill and
   * seeds its inputs. Fully editable, never auto-runs.
   */
  const openInAgentTester = () => {
    if (!agent) return;
    setPrefill({
      target: "agent-test",
      values: {
        agent_id: agent.id,
        input_state: before ?? null,
      },
    });
    router.push(`/agents/${agent.id}/edit?tab=test`);
    onOpenChange(false);
  };

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
                {/* Prompt tab visible when EITHER the runtime captured a
                    classical prompt (prompt_xml — api-call / claude_cli /
                    etc.) OR the callable recorded a prompt into its
                    structured output (#724 — populated by dap-cortex on
                    cortex nodes). Plain python-func without a recorded
                    prompt still hides the tab. */}
                {(data.runtime_id !== "python-func" ||
                  recordedPromptFrom(data.output_json) != null) && (
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

              {(data.runtime_id !== "python-func" ||
                recordedPromptFrom(data.output_json) != null) && (
                <TabsContent value="prompt">
                  {(() => {
                    const recorded = recordedPromptFrom(data.output_json);
                    // ``prompt_xml`` is "" (empty string) when an LLM node
                    // didn't capture one. Use ``||`` so we fall through to
                    // the recorded prompt in that case (?? would treat ""
                    // as a real value and short-circuit before recorded).
                    const prompt = data.prompt_xml || recorded;
                    if (!prompt) {
                      return (
                        <p className="text-xs text-muted-foreground">
                          No prompt recorded for this node.
                        </p>
                      );
                    }
                    return (
                      <>
                        {recorded != null && !data.prompt_xml && (
                          <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                            Recorded by the callable (#724) — `output_json.__prompt`
                          </p>
                        )}
                        <pre className="text-xs bg-muted p-3 rounded overflow-x-auto whitespace-pre-wrap">
                          {prompt}
                        </pre>
                      </>
                    );
                  })()}
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

            {/* #724 — richer footer: linked agent name + role + Cortex tag.
                Falls back to a short UUID if the agent hasn't resolved (cache
                miss, 404, or still loading) so the panel never blanks out. */}
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {agent ? (
                <>
                  <Link
                    href={`/agents/${agent.id}`}
                    className="font-medium text-foreground hover:underline"
                  >
                    {agent.name}
                  </Link>
                  <span className="text-muted-foreground">({agent.role})</span>
                </>
              ) : (
                <span>agent {data.agent_id.slice(0, 8)}…</span>
              )}
              <span>·</span>
              <span className="font-mono">{data.runtime_id}</span>
              {agent && isCortexCallable(agent.runtime_config) && (
                <span
                  className="rounded-sm border border-blue-500/40 bg-blue-500/10 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-blue-700 dark:text-blue-300"
                  title="Cortex callable — agent.runtime_config.callable_path starts with 'cortex.'"
                >
                  Cortex
                </span>
              )}
              {agent && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="ml-auto h-7"
                  onClick={openInAgentTester}
                  title="Stash this node's input state + jump to the agent's Test tab"
                >
                  <Wand2 className="mr-1 h-3.5 w-3.5" aria-hidden />
                  Open in agent tester
                </Button>
              )}
            </div>
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
  const { data, isLoading, isError } = useRunNodeExplain(runId, nodeId, open);

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
          <p className="font-medium text-foreground">{data.cause}</p>
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
          {!data.recognized && (
            <p className="text-muted-foreground">
              Heuristic match only — a richer AI explanation will appear here once an LLM is configured.
            </p>
          )}
        </>
      )}
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
