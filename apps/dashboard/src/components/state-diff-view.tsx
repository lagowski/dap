import type { PipelineState } from "@/lib/api/types";
import { computeStateDiff } from "@/lib/state-diff";

interface StateDiffViewProps {
  before: PipelineState | null;
  after: PipelineState;
}

export function StateDiffView({ before, after }: StateDiffViewProps) {
  if (!before) {
    return (
      <p className="text-xs text-muted-foreground">
        No previous state — this is the first node execution.
      </p>
    );
  }

  const diff = computeStateDiff(before, after);

  if (diff.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No state fields changed.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {diff.map((entry) => (
        <div key={entry.field} className="rounded border p-3">
          <div className="text-xs font-medium font-mono mb-1">{entry.field}</div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span className="text-muted-foreground">Before:</span>
              <pre className="mt-0.5 bg-destructive/10 text-destructive p-1.5 rounded whitespace-pre-wrap break-all">
                {formatValue(entry.before)}
              </pre>
            </div>
            <div>
              <span className="text-muted-foreground">After:</span>
              <pre className="mt-0.5 bg-green-500/10 text-green-700 dark:text-green-400 p-1.5 rounded whitespace-pre-wrap break-all">
                {formatValue(entry.after)}
              </pre>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}
