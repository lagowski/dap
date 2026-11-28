"use client";

import { useEffect, useRef } from "react";
import { useToast } from "@/components/ui/toast";
import type { Run } from "@/lib/api/types";
import { RUN_ID_PREFIX_LENGTH } from "./runs-filters";

function _expiryLabel(gateExpiresAt: string | null): string {
  if (!gateExpiresAt) return "";
  const remaining = new Date(gateExpiresAt).getTime() - Date.now();
  if (remaining <= 0) return " — approval window expired";
  const totalMins = Math.floor(remaining / 60_000);
  const hours = Math.floor(totalMins / 60);
  const mins = totalMins % 60;
  const label = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
  return ` — expires in ${label}`;
}

export function usePausedRunToasts(runs: Run[] | undefined) {
  const toast = useToast();
  const seenPausedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!runs) return;
    for (const run of runs) {
      if (
        run.final_status === "paused" &&
        run.paused_at_node &&
        !seenPausedRef.current.has(run.id)
      ) {
        seenPausedRef.current.add(run.id);
        toast({
          title: `Run ${run.id.slice(0, RUN_ID_PREFIX_LENGTH)} paused`,
          description: `Waiting at ${run.paused_at_node}${_expiryLabel(run.gate_expires_at)} - review and approve`,
          variant: "warning",
        });
      }
    }
  }, [runs, toast]);
}
