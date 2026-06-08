/**
 * Pure adapters between the DAP pipeline model and React Flow's
 * Node / Edge shapes (audit D1 split).
 *
 * The conversion is intentionally one-way: React Flow uses these
 * to render, but the source of truth for save / validate stays in
 * the original ``PipelineNode`` / ``PipelineEdge`` shape (mapped
 * back by ``buildPayload`` in ``use-pipeline-save``).
 */

import { MarkerType } from "@xyflow/react";
import type { Edge, Node, XYPosition } from "@xyflow/react";

import type { PipelineEdge, PipelineNode } from "@/lib/api/types";

type EdgeCondition = NonNullable<PipelineEdge["condition"]>;

/**
 * Short human label for an edge condition (#765) — so a conditional edge shows
 * *what* it branches on, not a bare "if". Comparison → ``field op value``;
 * logical → its comparisons joined with AND/OR (truncated). The ``extensions.``
 * prefix is dropped for brevity.
 */
export function summarizeCondition(condition: EdgeCondition): string {
  if (condition.type === "comparison") {
    const field = condition.field.replace(/^extensions\./, "");
    return `${field} ${condition.operator} ${String(condition.value)}`;
  }
  const joiner = condition.type === "or" ? " OR " : " AND ";
  const parts = condition.children.slice(0, 2).map(summarizeCondition);
  const summary = parts.join(joiner);
  return condition.children.length > 2 ? `${summary} …` : summary;
}


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
    // Arrowhead so the flow direction is unambiguous (#765).
    markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 },
    label: e.condition ? summarizeCondition(e.condition) : undefined,
    animated: e.condition != null,
    data: { waypoints },
  };
}
