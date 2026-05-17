/**
 * Pure adapters between the DAP pipeline model and React Flow's
 * Node / Edge shapes (audit D1 split).
 *
 * The conversion is intentionally one-way: React Flow uses these
 * to render, but the source of truth for save / validate stays in
 * the original ``PipelineNode`` / ``PipelineEdge`` shape (mapped
 * back by ``buildPayload`` in ``use-pipeline-save``).
 */

import type { Edge, Node } from "@xyflow/react";

import type { PipelineEdge, PipelineNode } from "@/lib/api/types";


/** Edge stroke colors keyed by annotation state. */
export const EDGE_WARNING_STROKE = "#dc2626"; // red-600
export const EDGE_CONDITION_STROKE = "#3b82f6"; // blue-500
export const EDGE_DEFAULT_STROKE = "#94a3b8"; // slate-400


export function toReactFlowNode(n: PipelineNode): Node {
  return {
    id: n.id,
    type: "default",
    position: n.position,
    data: { label: n.id, agentId: n.agent_id },
  };
}


export function toReactFlowEdge(e: PipelineEdge): Edge {
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.condition ? "if" : undefined,
    animated: e.condition != null,
  };
}
