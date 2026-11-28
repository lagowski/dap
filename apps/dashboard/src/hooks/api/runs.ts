"use client";

import {
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";
import * as api from "@/lib/api/client";
import type { RunCreateRequest } from "@/lib/api/types";
import {
  createEntityQuery,
  createInvalidatingMutation,
  refetchWhile,
} from "@/lib/query-factory";
import { queryKeys } from "./query-keys";

const RUNS_LIST_REFETCH_MS = 2_000;
const RUN_DETAIL_REFETCH_MS = 2_000;
const RUN_DETAIL_BURST_MS = 500; // fast poll right after approve

/** A run still needs live updates while running OR paused — paused runs
 * must pick up gate_payload / approval state as soon as it changes. */
const isRunBusy = (r: { final_status: string }) =>
  r.final_status === "running" || r.final_status === "paused";

export function useRunsList(
  filters?: {
    pipelineId?: string;
    finalStatus?: string;
    statuses?: string[];
    from?: string;
    to?: string;
    projectId?: string;
  },
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: queryKeys.runsList(filters),
    queryFn: () => api.listRuns(filters),
    enabled: options?.enabled ?? true,
    refetchInterval: refetchWhile(
      (data) => data.items.some(isRunBusy),
      RUNS_LIST_REFETCH_MS,
    ),
  });
}

export function useRun(id: string | null, opts?: { live?: boolean }) {
  return useQuery({
    queryKey: id ? queryKeys.run(id) : ["runs", "noop"],
    queryFn: id ? () => api.getRun(id) : skipToken,
    enabled: id != null,
    // The user can turn off live auto-refresh (#662 Phase 2). When ``live``
    // is explicitly false we never poll, regardless of run status; the page
    // exposes a manual Refresh button instead.
    refetchInterval:
      opts?.live === false
        ? false
        : refetchWhile(isRunBusy, RUN_DETAIL_REFETCH_MS),
  });
}

export const useRunStateHistory = createEntityQuery({
  scope: "runs",
  suffix: ["history"],
  queryKey: queryKeys.runHistory,
  queryFn: api.getRunStateHistory,
});

export const useRunNodeLogs = createEntityQuery({
  scope: "runs",
  suffix: ["nodes"],
  queryKey: queryKeys.runNodeLogs,
  queryFn: api.getRunNodeLogs,
});

export function useRunNodeLog(runId: string | null, nodeId: string | null) {
  return useQuery({
    queryKey:
      runId && nodeId
        ? queryKeys.runNodeLog(runId, nodeId)
        : ["runs", "noop", "nodes", "noop"],
    queryFn:
      runId && nodeId ? () => api.getRunNodeLog(runId, nodeId) : skipToken,
    enabled: runId != null && nodeId != null,
  });
}

/**
 * On-demand error explanation for a failed node (#691). Disabled until
 * ``enabled`` flips true (the "Explain this error" button) so we never spend
 * a request — or render an empty box — for nodes the user hasn't asked about.
 */
export function useRunNodeExplain(
  runId: string | null,
  nodeId: string | null,
  enabled: boolean,
  ai = false,
) {
  return useQuery({
    queryKey:
      runId && nodeId
        ? [...queryKeys.runNodeExplain(runId, nodeId), ai ? "ai" : "det"]
        : ["runs", "noop", "nodes", "noop", "explain"],
    queryFn:
      runId && nodeId
        ? () => api.getNodeErrorExplanation(runId, nodeId, ai)
        : skipToken,
    enabled: enabled && runId != null && nodeId != null,
  });
}

/** Lifecycle actions share the same invalidation: the run + every list. */
const runActionInvalidates = (runId: string) => [
  queryKeys.run(runId),
  queryKeys.runs,
];

export const useAbortRun = createInvalidatingMutation({
  mutationFn: api.abortRun,
  invalidates: runActionInvalidates,
});

export const usePauseRun = createInvalidatingMutation({
  mutationFn: api.pauseRun,
  invalidates: runActionInvalidates,
});

export const useResumeRun = createInvalidatingMutation({
  mutationFn: api.resumeRun,
  invalidates: runActionInvalidates,
});

export const useDeleteRun = createInvalidatingMutation({
  mutationFn: api.deleteRun,
  invalidates: runActionInvalidates,
});

export function useApproveGate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, nodeId }: { runId: string; nodeId: string }) =>
      api.approveGate(runId, nodeId),
    // The engine may still be committing "paused" to the DB when the user
    // clicks — retry once after a short delay so the user never has to
    // click twice.
    retry: (failureCount, error) =>
      failureCount < 1 &&
      error instanceof api.ApiError &&
      (error.status === 409 ||
        String(error.detail).toLowerCase().includes("not paused")),
    retryDelay: 1200,
    onSuccess: (_data, { runId }) => {
      // Immediate invalidate + burst-poll for 3s so the UI snaps to
      // "running" without waiting for the normal 2s interval.
      qc.invalidateQueries({ queryKey: queryKeys.run(runId) });
      qc.invalidateQueries({ queryKey: queryKeys.runs });
      const burst = setInterval(() => {
        qc.invalidateQueries({ queryKey: queryKeys.run(runId) });
      }, RUN_DETAIL_BURST_MS);
      setTimeout(() => clearInterval(burst), 3_000);
    },
    onError: (_err, { runId }) => {
      qc.invalidateQueries({ queryKey: queryKeys.run(runId) });
      qc.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });
}

export const usePipelineVersions = createEntityQuery({
  scope: "pipelines",
  suffix: ["versions"],
  queryKey: queryKeys.pipelineVersions,
  queryFn: api.listPipelineVersions,
});

/** Returns formatted time remaining until `expiresAt` and whether it's urgent (< 5 min). */
export function useGateCountdown(expiresAt: string | null | undefined): {
  label: string | null;
  isUrgent: boolean;
} {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!expiresAt) return;
    const id = setInterval(() => setNow(Date.now()), 10_000);
    return () => clearInterval(id);
  }, [expiresAt]);

  if (!expiresAt) return { label: null, isUrgent: false };

  const remaining = new Date(expiresAt).getTime() - now;
  if (remaining <= 0) return { label: "expired", isUrgent: true };

  const totalMins = Math.floor(remaining / 60_000);
  const hours = Math.floor(totalMins / 60);
  const mins = totalMins % 60;
  const label = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
  return { label, isUrgent: totalMins < 5 };
}

export const useTriggerRun = createInvalidatingMutation({
  mutationFn: (payload: RunCreateRequest) => api.triggerRun(payload),
  invalidates: () => [queryKeys.runs],
});
