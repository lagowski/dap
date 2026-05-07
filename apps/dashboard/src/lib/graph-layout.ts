/**
 * Dagre auto-layout for pipeline run graphs (#225).
 *
 * Used only in the read-only run detail view — the pipeline designer
 * preserves manual node positions and does not call this.
 */
import dagre from "dagre";
import type { Edge, Node } from "@xyflow/react";

const NODE_WIDTH = 160;
const NODE_HEIGHT = 40;

/**
 * Compute left-to-right (LR) dagre layout for a set of nodes and edges.
 * Returns new node/edge arrays with updated `position` values.
 * Existing position data is ignored — dagre owns layout in run view.
 */
export function getLayoutedElements(
  nodes: Node[],
  edges: Edge[],
  options: { direction?: "LR" | "TB"; nodesep?: number; ranksep?: number } = {},
): { nodes: Node[]; edges: Edge[] } {
  const { direction = "LR", nodesep = 80, ranksep = 120 } = options;

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep, ranksep });

  for (const node of nodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const { x, y } = g.node(node.id);
    return {
      ...node,
      position: {
        x: x - NODE_WIDTH / 2,
        y: y - NODE_HEIGHT / 2,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
}
