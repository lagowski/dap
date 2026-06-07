import { AlertTriangle } from "lucide-react";

import type { PipelineReadiness } from "@/lib/api/types";

/**
 * Pre-run readiness warning (#710). Surfaces, before you click Run, which
 * python-func nodes can't resolve their callable on this engine (e.g.
 * ``dap-cortex`` not installed, a wrong ``callable_path``) — the #1 cryptic
 * cause of mid-run cortex failures. The trigger still rejects such a run with a
 * 422; this just shows *why* up front. Renders nothing when everything resolves
 * (or there's nothing to resolve), so it only appears when it matters.
 */
export function PipelineReadinessNotice({
  readiness,
}: {
  readiness: PipelineReadiness | undefined;
}) {
  if (!readiness) return null;
  const blocked = readiness.checks.filter((c) => !c.resolvable);
  if (blocked.length === 0) return null;

  return (
    <div
      role="alert"
      className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm"
    >
      <div className="flex items-center gap-2 font-medium text-destructive">
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
        {blocked.length} node{blocked.length === 1 ? "" : "s"} can&apos;t run on this engine
      </div>
      <ul className="space-y-1.5">
        {blocked.map((c) => (
          <li key={c.node_id} className="text-muted-foreground">
            <span className="font-mono text-foreground">{c.node_id}</span>
            {c.callable_path ? (
              <>
                {" → "}
                <span className="font-mono">{c.callable_path}</span>
              </>
            ) : null}
            <div className="text-xs">{c.error}</div>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        Install the package providing the callable on the engine (e.g.{" "}
        <span className="font-mono">dap-cortex</span>), then retry.
      </p>
    </div>
  );
}
