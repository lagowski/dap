"use client";

import { useMemo, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import { ChevronLeft, ChevronRight, ExternalLink, Loader2, Sparkles } from "lucide-react";
import Link from "next/link";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { useRunNodeExplain, useRunNodeLog, useRunStateHistory } from "@/hooks/api";
import { NodeStatusBadge } from "@/components/status-badge";
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
                {/* python-func callables have no rendered prompt by design
                    (they receive state directly), so don't offer an empty
                    Prompt tab for them. */}
                {data.runtime_id !== "python-func" && (
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

              {data.runtime_id !== "python-func" && (
                <TabsContent value="prompt">
                  {data.prompt_xml ? (
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

            <div className="text-xs text-muted-foreground">
              <span className="font-mono">{data.runtime_id}</span>
              <span className="mx-2">·</span>
              <span>agent {data.agent_id}</span>
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
