"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import type { NodeStatus, Pipeline } from "@/lib/api/types";

interface PipelineGraphProps {
  pipeline: Pipeline;
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

export function PipelineGraph({
  pipeline,
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

  const edges = useMemo<Edge[]>(() => {
    return pipeline.edges.map((e) => ({
      id: e.id,
      source: e.source === "__start__" ? pipeline.entry_point : e.source,
      target: e.target,
      label: e.condition ? "if" : undefined,
      type: e.condition ? "step" : "default",
      animated: e.condition != null,
      style: { stroke: e.condition ? "#3b82f6" : "#94a3b8" },
    }));
  }, [pipeline.edges, pipeline.entry_point]);

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
