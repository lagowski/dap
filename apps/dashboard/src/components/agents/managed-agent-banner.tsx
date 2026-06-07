import Link from "next/link";
import { Boxes } from "lucide-react";

import { managedAgentInfo, type ManagedAgentLike } from "@/lib/managed-agent";

/**
 * Banner for managed (bundle-owned) agents (#739). Cortex agents are nodes of
 * an inseparable pipeline whose prompts/logic live in the ``dap-cortex``
 * package — not editable from DAP. Rather than letting each surface look like a
 * normal editable agent (and patching them one by one), we flag the whole agent
 * up front: only the wrapper is editable; the real prompt is per-run.
 *
 * Renders nothing for ordinary agents, so call sites can drop it in
 * unconditionally.
 */
export function ManagedAgentBanner({ agent }: { agent: ManagedAgentLike }) {
  const { managed, kind, callablePath } = managedAgentInfo(agent);
  if (!managed) return null;

  const label = kind === "cortex" ? "Cortex" : "a plugin";
  return (
    <div className="flex gap-3 rounded-md border border-amber-300/60 bg-amber-50 p-3 text-sm dark:border-amber-700/50 dark:bg-amber-950/30">
      <Boxes className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
      <div className="space-y-1">
        <p className="font-medium text-amber-900 dark:text-amber-200">
          Managed by {label} — configured in the dap-cortex package, not here.
        </p>
        <p className="text-amber-800/90 dark:text-amber-300/80">
          This agent is one node of an inseparable pipeline. Its prompt and logic live in the
          callable
          {callablePath ? (
            <>
              {" "}
              (<span className="font-mono">{callablePath}</span>)
            </>
          ) : null}
          , so only the wrapper (name, timeout, budget) is meaningfully editable here. The actual
          prompt it sends is built per-run — see it on a{" "}
          <Link href="/runs" className="underline hover:no-underline">
            recent run
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
