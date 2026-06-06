"use client";

import { useMemo } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRunNodeLog, useRunStateHistory } from "@/hooks/api";
import { NodeStatusBadge } from "@/components/status-badge";
import { StateDiffView } from "@/components/state-diff-view";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";
import type { PipelineState, StateSnapshot } from "@/lib/api/types";

interface NodeDetailPanelProps {
  runId: string;
  nodeId: string | null;
  onOpenChange: (open: boolean) => void;
}

export function NodeDetailPanel({ runId, nodeId, onOpenChange }: NodeDetailPanelProps) {
  const open = nodeId != null;
  const { data, isPending, isError, error } = useRunNodeLog(runId, nodeId);
  const { data: stateHistory } = useRunStateHistory(open ? runId : null);

  const { before, after } = useMemo(() => {
    return getSnapshotPair(stateHistory ?? [], nodeId);
  }, [stateHistory, nodeId]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Node: <span className="font-mono">{nodeId}</span>
            {data && <NodeStatusBadge status={data.status} />}
          </DialogTitle>
        </DialogHeader>

        {isPending && nodeId && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}

        {isError && (
          <p className="text-sm text-destructive">
            Failed to load: {(error as Error).message}
          </p>
        )}

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
                <TabsTrigger value="prompt" className="flex-1">
                  Prompt
                </TabsTrigger>
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

              <TabsContent value="prompt">
                <pre className="text-xs bg-muted p-3 rounded overflow-x-auto whitespace-pre-wrap">
                  {data.prompt_xml}
                </pre>
              </TabsContent>

              <TabsContent value="state-diff">
                {after ? (
                  <StateDiffView before={before} after={after} />
                ) : (
                  <p className="text-xs text-muted-foreground">
                    No state history available for this node.
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
