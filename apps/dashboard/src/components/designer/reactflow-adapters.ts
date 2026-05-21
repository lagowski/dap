/**
 * Pure adapters between the DAP pipeline model and React Flow's
 * Node / Edge shapes (audit D1 split).
 *
 * The conversion is intentionally one-way: React Flow uses these
 * to render, but the source of truth for save / validate stays in
 * the original ``PipelineNode`` / ``PipelineEdge`` shape (mapped
 * back by ``buildPayload`` in ``use-pipeline-save``).
 */

import type { Edge, Node, XYPosition } from "@xyflow/react";

import type { PipelineEdge, PipelineNode } from "@/lib/api/types";


/** Edge stroke colors keyed by annotation state. */
export const EDGE_WARNING_STROKE = "#dc2626"; // red-600
export const EDGE_CONDITION_STROKE = "#3b82f6"; // blue-500
export const EDGE_DEFAULT_STROKE = "#94a3b8"; // slate-400

export type EdgeWaypoints = Record<string, XYPosition[]>;

function validWaypoint(value: unknown): value is XYPosition {
  if (!value || typeof value !== "object") return false;
  const point = value as Partial<XYPosition>;
  return (
    typeof point.x === "number" &&
    typeof point.y === "number" &&
    Number.isFinite(point.x) &&
    Number.isFinite(point.y)
  );
}

export function parseEdgeWaypoints(
  uiMetadata: Record<string, unknown> | null | undefined,
): EdgeWaypoints {
  const raw = uiMetadata?.edge_waypoints;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};

  const parsed: EdgeWaypoints = {};
  for (const [edgeId, value] of Object.entries(raw)) {
    if (!Array.isArray(value)) continue;
    const waypoints = value.filter(validWaypoint).map((point) => ({
      x: point.x,
      y: point.y,
    }));
    if (waypoints.length > 0) parsed[edgeId] = waypoints;
  }
  return parsed;
}


export function toReactFlowNode(n: PipelineNode): Node {
  return {
    id: n.id,
    type: "default",
    position: n.position,
    data: { label: n.id, agentId: n.agent_id },
  };
}


export function toReactFlowEdge(e: PipelineEdge, waypoints: XYPosition[] = []): Edge {
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    type: "waypoint",
    label: e.condition ? "if" : undefined,
    animated: e.condition != null,
    data: { waypoints },
  };
}
