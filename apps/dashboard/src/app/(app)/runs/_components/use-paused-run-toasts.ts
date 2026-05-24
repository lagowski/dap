"use client";

import { useEffect, useRef } from "react";
import { useToast } from "@/components/ui/toast";
import type { Run } from "@/lib/api/types";
import { RUN_ID_PREFIX_LENGTH } from "./runs-filters";

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
          description: `Waiting at ${run.paused_at_node} - review and approve`,
          variant: "warning",
        });
      }
    }
  }, [runs, toast]);
}
