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
}

const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: "#cbd5e1",
  running: "#3b82f6",
  success: "#10b981",
  failed: "#ef4444",
  skipped: "#f59e0b",
};

const EDGE_WARNING_STROKE = "#dc2626"; // red-600
const EDGE_CONDITION_STROKE = "#3b82f6"; // blue-500
const EDGE_DEFAULT_STROKE = "#94a3b8"; // slate-400

export function PipelineGraph({
  pipeline,
  agents,
  nodeStatuses = {},
  currentNode,
  onNodeClick,
}: PipelineGraphProps) {
  const nodes = useMemo<Node[]>(() => {
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

  const edges = useMemo<Edge[]>(() => {
    return pipeline.edges.map((e) => {
      const annotation: EdgeAnnotation = annotations.get(e.id) ?? {
        fields: [],
        warning: false,
        unknown: false,
      };
      const hasCondition = e.condition != null;
      const label = formatEdgeLabel(annotation, hasCondition);
      const tooltip = annotationTooltip(annotation);
      const stroke = annotation.warning
        ? EDGE_WARNING_STROKE
        : hasCondition
          ? EDGE_CONDITION_STROKE
          : EDGE_DEFAULT_STROKE;
      return {
        id: e.id,
        source: e.source === "__start__" ? pipeline.entry_point : e.source,
        target: e.target,
        label,
        labelStyle: annotation.warning
          ? { fill: EDGE_WARNING_STROKE, fontWeight: 600 }
          : undefined,
        labelBgStyle: annotation.warning
          ? { fill: "#fee2e2" /* red-100 */ }
          : undefined,
        type: hasCondition ? "step" : "default",
        animated: hasCondition,
        style: { stroke, strokeWidth: annotation.warning ? 2 : 1 },
        // React Flow forwards `data` to custom edge components; for the
        // default renderer we tuck the tooltip text here so other code
        // (e.g. inspector, snapshots) can read it without re-computing.
        data: { tooltip, annotation },
      };
    });
  }, [pipeline.edges, pipeline.entry_point, annotations]);

  const validEdges = edges.filter(
    (e) => e.source && e.target && e.target !== "__end__",
  );

  return (
    <div className="h-[500px] w-full rounded-md border bg-background">
      <ReactFlow
        nodes={nodes}
        edges={validEdges}
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
