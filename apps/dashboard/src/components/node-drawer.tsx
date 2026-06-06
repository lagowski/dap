"use client";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { LoadingState } from "@/components/ui/spinner";
import { useRunNodeLog } from "@/hooks/api";
import { NodeStatusBadge } from "@/components/status-badge";
import { formatCost, formatDuration, formatTokens } from "@/lib/utils";

interface NodeDrawerProps {
  runId: string;
  nodeId: string | null;
  onOpenChange: (open: boolean) => void;
}

export function NodeDrawer({ runId, nodeId, onOpenChange }: NodeDrawerProps) {
  const open = nodeId != null;
  const { data, isPending, isError, error } = useRunNodeLog(runId, nodeId);

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
          <LoadingState />
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

            <Section title="Prompt (XML)">
              <pre className="text-xs bg-muted p-3 rounded overflow-x-auto whitespace-pre-wrap">
                {data.prompt_xml}
              </pre>
            </Section>

            {data.stdout && (
              <Section title="Output">
                <pre className="text-xs bg-muted p-3 rounded overflow-x-auto whitespace-pre-wrap">
                  {data.stdout}
                </pre>
              </Section>
            )}

            {data.output_json && (
              <Section title="Structured output">
                <pre className="text-xs bg-muted p-3 rounded overflow-x-auto">
                  {JSON.stringify(data.output_json, null, 2)}
                </pre>
              </Section>
            )}

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
