"use client";

import { usePipelinesList } from "@/hooks/api";
import type { Pipeline } from "@/lib/api/types";

interface GateAutoApproveEditorProps {
  pipelines: Record<string, string>;
  selectedNodes: string[];
  onChange: (next: string[]) => void;
}

function toggleString(list: readonly string[], value: string, checked: boolean): string[] {
  const current = new Set(list);
  if (checked) {
    current.add(value);
  } else {
    current.delete(value);
  }
  return [...current];
}

export function GateAutoApproveEditor({
  pipelines,
  selectedNodes,
  onChange,
}: GateAutoApproveEditorProps) {
  const { data: pipelinesList } = usePipelinesList();
  const pipelineById = new Map(
    (pipelinesList?.items ?? []).map((pipeline) => [pipeline.id, pipeline]),
  );
  const rows = buildGateAutoApproveRows(pipelines, selectedNodes, pipelineById);

  if (rows.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No approval gates found in the bound pipelines.
      </p>
    );
  }

  return (
    <div className="rounded-md border">
      <div className="grid grid-cols-[1fr_auto] gap-3 border-b px-3 py-2 text-xs font-medium text-muted-foreground">
        <span>Gate node</span>
        <span>Auto-approve</span>
      </div>
      <div className="divide-y">
        {rows.map((row) => (
          <label
            key={row.nodeId}
            className="grid grid-cols-[1fr_auto] items-center gap-3 px-3 py-2 text-sm"
          >
            <span>
              <span className="font-mono">{row.nodeId}</span>
              {row.pipelineNames.length > 0 ? (
                <span className="ml-2 text-xs text-muted-foreground">
                  {row.pipelineNames.join(", ")}
                </span>
              ) : (
                <span className="ml-2 text-xs text-yellow-600">
                  not in current bindings
                </span>
              )}
            </span>
            <input
              type="checkbox"
              className="h-4 w-4 accent-primary"
              checked={selectedNodes.includes(row.nodeId)}
              aria-describedby={`gate-auto-approve-${row.nodeId}-description`}
              onChange={(event) =>
                onChange(toggleString(selectedNodes, row.nodeId, event.target.checked))
              }
              aria-label={`Auto-approve ${row.nodeId}`}
            />
            <span id={`gate-auto-approve-${row.nodeId}-description`} className="sr-only">
              Auto-resume this approval gate for project-triggered runs.
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

interface GateAutoApproveRow {
  nodeId: string;
  pipelineNames: string[];
}

export function buildGateAutoApproveRows(
  bindings: Record<string, string>,
  selectedNodes: readonly string[],
  pipelineById: ReadonlyMap<string, Pick<Pipeline, "id" | "name" | "defaults">>,
): GateAutoApproveRow[] {
  const namesByNode = new Map<string, Set<string>>();
  for (const pipelineId of Object.values(bindings)) {
    const pipeline = pipelineById.get(pipelineId);
    if (!pipeline) continue;
    for (const nodeId of pipeline.defaults?.approval_required_nodes ?? []) {
      const names = namesByNode.get(nodeId) ?? new Set<string>();
      names.add(pipeline.name);
      namesByNode.set(nodeId, names);
    }
  }

  for (const nodeId of selectedNodes) {
    if (!namesByNode.has(nodeId)) {
      namesByNode.set(nodeId, new Set());
    }
  }

  return [...namesByNode.entries()]
    .map(([nodeId, pipelineNames]) => ({
      nodeId,
      pipelineNames: [...pipelineNames].sort((a, b) => a.localeCompare(b)),
    }))
    .sort((a, b) => a.nodeId.localeCompare(b.nodeId));
}
