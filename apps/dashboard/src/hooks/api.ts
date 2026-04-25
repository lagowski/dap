"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";
import type { AgentCreate, PipelineCreate, PipelineUpdate } from "@/lib/api/types";

export const queryKeys = {
  runs: ["runs"] as const,
  runsList: (filters?: { pipelineId?: string; finalStatus?: string }) =>
    ["runs", "list", filters ?? {}] as const,
  run: (id: string) => ["runs", id] as const,
  runState: (id: string) => ["runs", id, "state"] as const,
  runHistory: (id: string) => ["runs", id, "history"] as const,
  runNodeLog: (runId: string, nodeId: string) =>
    ["runs", runId, "nodes", nodeId] as const,
  agents: ["agents"] as const,
  agentsList: (filters?: { role?: string }) => ["agents", "list", filters ?? {}] as const,
  pipelines: ["pipelines"] as const,
  pipelinesList: ["pipelines", "list"] as const,
  pipeline: (id: string) => ["pipelines", id] as const,
};

const RUNS_LIST_REFETCH_MS = 2_000;
const RUN_DETAIL_REFETCH_MS = 2_000;

export function useRunsList(filters?: {
  pipelineId?: string;
  finalStatus?: string;
}) {
  return useQuery({
    queryKey: queryKeys.runsList(filters),
    queryFn: () => api.listRuns(filters),
    refetchInterval: (query) => {
      // Stop polling when there are no running runs
      const data = query.state.data;
      if (data && !data.items.some((r) => r.final_status === "running")) {
        return false;
      }
      return RUNS_LIST_REFETCH_MS;
    },
  });
}

export function useRun(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.run(id) : ["runs", "noop"],
    queryFn: () => (id ? api.getRun(id) : Promise.reject(new Error("no id"))),
    enabled: id != null,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && data.final_status !== "running") return false;
      return RUN_DETAIL_REFETCH_MS;
    },
  });
}

export function useRunStateHistory(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.runHistory(id) : ["runs", "noop", "history"],
    queryFn: () =>
      id ? api.getRunStateHistory(id) : Promise.reject(new Error("no id")),
    enabled: id != null,
  });
}

export function useRunNodeLog(runId: string | null, nodeId: string | null) {
  return useQuery({
    queryKey:
      runId && nodeId
        ? queryKeys.runNodeLog(runId, nodeId)
        : ["runs", "noop", "nodes", "noop"],
    queryFn: () =>
      runId && nodeId
        ? api.getRunNodeLog(runId, nodeId)
        : Promise.reject(new Error("no id")),
    enabled: runId != null && nodeId != null,
  });
}

export function usePipeline(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.pipeline(id) : ["pipelines", "noop"],
    queryFn: () => (id ? api.getPipeline(id) : Promise.reject(new Error("no id"))),
    enabled: id != null,
  });
}

export function usePipelinesList() {
  return useQuery({
    queryKey: queryKeys.pipelinesList,
    queryFn: () => api.listPipelines(),
  });
}

export function useCreatePipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PipelineCreate) => api.createPipeline(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines });
    },
  });
}

export function useUpdatePipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: PipelineUpdate }) =>
      api.updatePipeline(id, payload),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines });
      qc.invalidateQueries({ queryKey: queryKeys.pipeline(variables.id) });
    },
  });
}

export function useValidatePipeline() {
  return useMutation({
    mutationFn: (payload: PipelineCreate) => api.validatePipeline(payload),
  });
}

export function useAgentsList(filters?: { role?: string }) {
  return useQuery({
    queryKey: queryKeys.agentsList(filters),
    queryFn: () => api.listAgents(filters),
  });
}

export function useCreateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AgentCreate) => api.createAgent(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.agents });
    },
  });
}
