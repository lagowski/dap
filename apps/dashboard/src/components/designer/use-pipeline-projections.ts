"use client";

/**
 * Derived projections from designer state (audit D1 split).
 *
 * The designer carries React Flow's ``Node[]`` / ``Edge[]`` shapes
 * + a separate ``edgeMeta`` map for designer-only fields
 * (condition, label). Three pieces of UI need a different view of
 * that state:
 *
 * - The Inspector consumes ``PipelineNode[]`` / ``PipelineEdge[]``
 *   (the canonical API shapes).
 * - The canvas renders edges decorated with field-flow chips and
 *   warning styling.
 * - The Inspector also needs the selected node/edge as a tagged
 *   union so its discriminator can route to the right panel.
 *
 * All three derive from the same state, so they live in one hook
 * to share the ``annotations`` computation (which the other two
 * read).
 */

import { useMemo } from "react";

import type { Edge, Node } from "@xyflow/react";

import {
  annotationTooltip,
  computeEdgeAnnotation,
  END_SENTINEL,
  formatEdgeLabel,
  START_SENTINEL,
  type EdgeAnnotation,
} from "@/lib/edge-annotations";
import type {
  Agent,
  EdgeCondition,
  PipelineEdge,
  PipelineNode,
} from "@/lib/api/types";

import {
  EDGE_CONDITION_STROKE,
  EDGE_DEFAULT_STROKE,
  EDGE_WARNING_STROKE,
} from "./reactflow-adapters";
import type { DesignerEdge, DesignerNode } from "./types";


interface UsePipelineProjectionsParams {
  nodes: Node[];
  edges: Edge[];
  edgeMeta: Record<string, { condition: EdgeCondition | null; label: string | null }>;
  agents: Agent[];
  selection:
    | { kind: "node"; id: string }
    | { kind: "edge"; id: string }
    | { kind: "none" };
}


interface UsePipelineProjectionsResult {
  /** Canonical-shape node list — handed to the Inspector. */
  designerNodes: PipelineNode[];
  /** Canonical-shape edge list — handed to the Inspector. */
  designerEdges: PipelineEdge[];
  /** edge_id → field-flow annotation. Single source of truth. */
  annotations: Map<string, EdgeAnnotation>;
  /** React Flow edges decorated with field-flow chips + warning styling. */
  annotatedEdges: Edge[];
  /** Selection as a tagged union, ready for the Inspector discriminator. */
  selectionDetail:
    | { kind: "node"; node: DesignerNode }
    | { kind: "edge"; edge: DesignerEdge; annotation: EdgeAnnotation }
    | { kind: "none" };
}


export function usePipelineProjections({
  nodes,
  edges,
  edgeMeta,
  agents,
  selection,
}: UsePipelineProjectionsParams): UsePipelineProjectionsResult {
  // Lifted from buildPayload so the Inspector can reuse the projections.
  const designerNodes = useMemo<PipelineNode[]>(
    () =>
      nodes.map((n) => ({
        id: n.id,
        agent_id: String((n.data as { agentId?: string })?.agentId ?? ""),
        position: { x: n.position.x, y: n.position.y },
      })),
    [nodes],
  );
  const designerEdges = useMemo<PipelineEdge[]>(
    () =>
      edges.map((e) => {
        const meta = edgeMeta[e.id];
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          condition: meta?.condition ?? null,
          label: meta?.label ?? null,
        };
      }),
    [edges, edgeMeta],
  );

  // Single source-of-truth map: edge_id → EdgeAnnotation. Both the
  // rendered edges and the inspector read from this so the chip on
  // the canvas and the field list in the side panel can never drift.
  const annotations = useMemo<Map<string, EdgeAnnotation>>(() => {
    const agentsById = new Map<string, Agent>(agents.map((a) => [a.id, a]));
    const agentForReactFlowNode = (nodeId: string): Agent | undefined => {
      // Sentinels never resolve to an agent — return undefined so the
      // empty-annotation branch below kicks in (no chip, no warning).
      if (nodeId === START_SENTINEL || nodeId === END_SENTINEL) {
        return undefined;
      }
      const node = nodes.find((n) => n.id === nodeId);
      if (node === undefined) return undefined;
      const agentId = String((node.data as { agentId?: string })?.agentId ?? "");
      return agentsById.get(agentId);
    };
    const out = new Map<string, EdgeAnnotation>();
    for (const e of edges) {
      // Sentinel-touching edges have no contract to evaluate.
      if (e.target === END_SENTINEL || e.source === START_SENTINEL) {
        out.set(e.id, { fields: [], warning: false, unknown: false });
        continue;
      }
      out.set(
        e.id,
        computeEdgeAnnotation(
          agentForReactFlowNode(e.source),
          agentForReactFlowNode(e.target),
        ),
      );
    }
    return out;
  }, [edges, nodes, agents]);

  // Decorate React Flow edges with field annotations (#62). Original
  // ``edges`` stays the source of truth; we only rewrite cosmetic
  // props (label / style / data) using the precomputed map above.
  const annotatedEdges = useMemo<Edge[]>(() => {
    return edges.map((e) => {
      const annotation =
        annotations.get(e.id) ?? { fields: [], warning: false, unknown: false };
      const meta = edgeMeta[e.id];
      const hasCondition = meta?.condition != null;
      const userLabel = meta?.label?.trim() ?? "";
      const annotationLabel = formatEdgeLabel(annotation, hasCondition);
      // User-set edge label takes priority — they wrote it deliberately.
      // When unset we fall back to the auto annotation chip.
      const label = userLabel.length > 0 ? userLabel : annotationLabel;
      const stroke = annotation.warning
        ? EDGE_WARNING_STROKE
        : hasCondition
          ? EDGE_CONDITION_STROKE
          : EDGE_DEFAULT_STROKE;
      return {
        ...e,
        label,
        labelStyle: annotation.warning
          ? { fill: EDGE_WARNING_STROKE, fontWeight: 600 }
          : undefined,
        labelBgStyle: annotation.warning ? { fill: "#fee2e2" } : undefined,
        animated: hasCondition,
        style: { stroke, strokeWidth: annotation.warning ? 2 : 1 },
        data: {
          ...(e.data as object | undefined),
          tooltip: annotationTooltip(annotation),
          annotation,
        },
      };
    });
  }, [edges, edgeMeta, annotations]);

  // Compute current selection details for inspector. Pulls the
  // edge's annotation from the shared ``annotations`` map so the
  // inspector view never disagrees with the chip on the canvas.
  const selectionDetail = useMemo<UsePipelineProjectionsResult["selectionDetail"]>(() => {
    if (selection.kind === "node") {
      const n = nodes.find((x) => x.id === selection.id);
      if (!n) return { kind: "none" };
      const node: DesignerNode = {
        id: n.id,
        agent_id: String((n.data as { agentId?: string })?.agentId ?? ""),
        position: { x: n.position.x, y: n.position.y },
      };
      return { kind: "node", node };
    }
    if (selection.kind === "edge") {
      const e = edges.find((x) => x.id === selection.id);
      if (!e) return { kind: "none" };
      const meta = edgeMeta[e.id];
      const edge: DesignerEdge = {
        id: e.id,
        source: e.source,
        target: e.target,
        condition: meta?.condition ?? null,
        label: meta?.label ?? null,
      };
      const annotation =
        annotations.get(e.id) ?? { fields: [], warning: false, unknown: false };
      return { kind: "edge", edge, annotation };
    }
    return { kind: "none" };
  }, [selection, nodes, edges, edgeMeta, annotations]);

  return {
    designerNodes,
    designerEdges,
    annotations,
    annotatedEdges,
    selectionDetail,
  };
}
