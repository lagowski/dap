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
import { queryKeys } from "./query-keys";

const RUNS_LIST_REFETCH_MS = 2_000;
const RUN_DETAIL_REFETCH_MS = 2_000;
const RUN_DETAIL_BURST_MS = 500; // fast poll right after approve

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
    refetchInterval: (query) => {
      // Keep polling while any run is running or paused (paused runs
      // need live updates for gate approval detection).
      const data = query.state.data;
      if (
        data &&
        !data.items.some(
          (r) => r.final_status === "running" || r.final_status === "paused",
        )
      ) {
        return false;
      }
      return RUNS_LIST_REFETCH_MS;
    },
  });
}

export function useRun(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.run(id) : ["runs", "noop"],
    queryFn: id ? () => api.getRun(id) : skipToken,
    enabled: id != null,
    refetchInterval: (query) => {
      const data = query.state.data;
      // Keep polling while running OR paused (paused needs to pick up
      // gate_payload as soon as the interrupt fires).
      if (data && data.final_status !== "running" && data.final_status !== "paused") return false;
      return RUN_DETAIL_REFETCH_MS;
    },
  });
}

export function useRunStateHistory(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.runHistory(id) : ["runs", "noop", "history"],
    queryFn: id ? () => api.getRunStateHistory(id) : skipToken,
    enabled: id != null,
  });
}

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

function useRunActionMutation<T>(action: (id: string) => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: action,
    onSuccess: (_data, runId) => {
      qc.invalidateQueries({ queryKey: queryKeys.run(runId) });
      qc.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });
}

export function useAbortRun() {
  return useRunActionMutation(api.abortRun);
}

export function usePauseRun() {
  return useRunActionMutation(api.pauseRun);
}

export function useResumeRun() {
  return useRunActionMutation(api.resumeRun);
}

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

export function usePipelineVersions(
  id: string | null,
  options?: { enabled?: boolean },
) {
  const enabled = (options?.enabled ?? true) && id != null;
  return useQuery({
    queryKey: id ? queryKeys.pipelineVersions(id) : ["pipelines", "noop", "versions"],
    queryFn: id ? () => api.listPipelineVersions(id) : skipToken,
    enabled,
  });
}

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

export function useTriggerRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: RunCreateRequest) => api.triggerRun(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });
}
