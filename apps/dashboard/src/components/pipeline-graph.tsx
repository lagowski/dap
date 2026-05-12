"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import {
  annotateEdges,
  annotationTooltip,
  formatEdgeLabel,
  type EdgeAnnotation,
} from "@/lib/edge-annotations";
import { getLayoutedElements } from "@/lib/graph-layout";
import { getEdgeColor, getEdgeLabel } from "@/lib/edge-color";
import type { Agent, NodeStatus, Pipeline } from "@/lib/api/types";

interface PipelineGraphProps {
  pipeline: Pipeline;
  /**
   * Agents referenced by the pipeline. Used to compute per-edge field
   * annotations (#62) — the chip on each edge shows which state fields
   * flow from source's ``output_schema`` to target's ``input_schema``.
   * Pass omitted/empty list to keep the legacy plain-edge rendering.
   */
  agents?: readonly Agent[];
  nodeStatuses?: Record<string, NodeStatus>;
  currentNode?: string | null;
  onNodeClick?: (nodeId: string) => void;
  /**
   * When true, applies dagre LR auto-layout instead of using saved node
   * positions (#225). Set in the run detail view (read-only); leave false
   * in the pipeline designer so manual positioning is preserved.
   */
  autoLayout?: boolean;
}

const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: "#cbd5e1",
  running: "#3b82f6",
  success: "#10b981",
  failed: "#ef4444",
  skipped: "#f59e0b",
};

const EDGE_WARNING_STROKE = "#dc2626"; // red-600
const EDGE_DEFAULT_STROKE = "#94a3b8"; // slate-400

export function PipelineGraph({
  pipeline,
  agents,
  nodeStatuses = {},
  currentNode,
  onNodeClick,
  autoLayout = false,
}: PipelineGraphProps) {
  const rawNodes = useMemo<Node[]>(() => {
    return pipeline.nodes.map((n, idx) => {
      const status: NodeStatus = nodeStatuses[n.id] ?? "pending";
      const isCurrent = n.id === currentNode;
      return {
        id: n.id,
        type: "default",
        position:
          n.position && (n.position.x !== 0 || n.position.y !== 0)
            ? n.position
            : { x: idx * 200, y: 0 },
        data: { label: n.id },
        style: {
          backgroundColor: "#fff",
          borderColor: STATUS_COLORS[status],
          borderWidth: isCurrent ? 3 : 2,
          borderStyle: "solid",
          borderRadius: 6,
          padding: 8,
          fontSize: 12,
          fontWeight: 500,
          minWidth: 120,
        },
      };
    });
  }, [pipeline.nodes, nodeStatuses, currentNode]);

  // Per-edge annotation: which fields flow through, and whether the
  // contract is broken (target declares inputs the source can't
  // produce). Sentinel handling (``__start__`` source / ``__end__``
  // target) is encapsulated by ``annotateEdges`` itself.
  const annotations = useMemo<Map<string, EdgeAnnotation>>(
    () => annotateEdges(pipeline, agents ?? []),
    [pipeline, agents],
  );

  const rawEdges = useMemo<Edge[]>(() => {
    return pipeline.edges.map((e) => {
      const annotation: EdgeAnnotation = annotations.get(e.id) ?? {
        fields: [],
        warning: false,
        unknown: false,
      };
      const hasCondition = e.condition != null;

      // Derive colour and label from structured condition (#227).
      // Warning overrides condition colour to keep the contract-error
      // signal prominent.
      const conditionStroke =
        e.condition != null ? getEdgeColor(e.condition) : EDGE_DEFAULT_STROKE;
      const stroke = annotation.warning ? EDGE_WARNING_STROKE : conditionStroke;

      // Build edge label (#227): semantic condition label ("✓ approved" etc.)
      // combined with the field-flow annotation chip when fields exist.
      // Use formatEdgeLabel with hasCondition=false so we get just the chip
      // without the bare "if" prefix (which would shadow conditionLabel).
      const fieldChip =
        annotation.fields.length > 0 || annotation.warning
          ? formatEdgeLabel(annotation, false)
          : undefined;
      const conditionLabel =
        e.condition != null ? getEdgeLabel(e.condition) : "";
      const labelParts = [conditionLabel, fieldChip].filter(Boolean);
      const label = labelParts.length > 0 ? labelParts.join(" · ") : undefined;

      const tooltip = annotationTooltip(annotation);
      return {
        id: e.id,
        source: e.source === "__start__" ? pipeline.entry_point : e.source,
        target: e.target,
        label,
        labelStyle: annotation.warning
          ? { fill: EDGE_WARNING_STROKE, fontWeight: 600 }
          : conditionLabel
            ? { fill: stroke, fontWeight: 500, fontSize: 11 }
            : undefined,
        labelBgStyle: annotation.warning
          ? { fill: "#fee2e2" /* red-100 */ }
          : undefined,
        type: hasCondition ? "step" : "default",
        animated: hasCondition,
        style: { stroke, strokeWidth: annotation.warning ? 2 : 1 },
        data: { tooltip, annotation },
      };
    });
  }, [pipeline.edges, pipeline.entry_point, annotations]);

  // Filter sentinel targets and optionally apply dagre layout (#225).
  // Both steps live in the same useMemo so the filter doesn't create a
  // new array reference on every render (which would re-trigger dagre).
  const { nodes, edges } = useMemo(() => {
    const validEdges = rawEdges.filter(
      (e) => e.source && e.target && e.target !== "__end__",
    );
    if (!autoLayout) return { nodes: rawNodes, edges: validEdges };
    return getLayoutedElements(rawNodes, validEdges);
  }, [autoLayout, rawNodes, rawEdges]);

  return (
    <div className="h-[500px] w-full rounded-md border bg-background">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        onNodeClick={(_e, node) => onNodeClick?.(node.id)}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
