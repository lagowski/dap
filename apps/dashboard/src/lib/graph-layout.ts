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
 *
 * When `maxPerRow > 0` and the graph has more dagre columns (ranks) than
 * `maxPerRow`, the columns are wrapped into multiple rows that read
 * left→right like text lines (see {@link wrapColumns}). When unset or 0 the
 * function behaves exactly as before — a single LR row.
 */
export function getLayoutedElements(
  nodes: Node[],
  edges: Edge[],
  options: {
    direction?: "LR" | "TB";
    nodesep?: number;
    ranksep?: number;
    maxPerRow?: number;
  } = {},
): { nodes: Node[]; edges: Edge[] } {
  const {
    direction = "LR",
    nodesep = 80,
    ranksep = 120,
    maxPerRow = 0,
  } = options;

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

  // Dagre centres each node on its (x, y); convert to top-left for React Flow.
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

  if (maxPerRow > 0) {
    return { nodes: wrapColumns(layoutedNodes, nodesep, maxPerRow), edges };
  }

  return { nodes: layoutedNodes, edges };
}

/**
 * Wrap the dagre columns of an LR layout into rows of at most `maxPerRow`
 * columns, so a long linear pipeline reads like wrapped text instead of one
 * very wide row.
 *
 * Algorithm:
 *  1. Group nodes by their dagre column (distinct rounded x); order columns
 *     left→right and index them 0..C-1.
 *  2. For column index `ci`: rowIdx = floor(ci / maxPerRow),
 *     colInRow = ci % maxPerRow.
 *  3. Place each column at grid cell (rowIdx, colInRow):
 *       cell x = colInRow * (NODE_WIDTH + nodesep)
 *       cell y = rowIdx * ROW_STEP
 *     where ROW_STEP is the tallest column's vertical extent across the whole
 *     graph plus a vertical gap, so wrapped rows never overlap even with
 *     branches.
 *  4. Preserve within-column structure: offset each node inside its cell by
 *     its original (y − columnMinY) so parallel/branch nodes stay stacked.
 *
 * Rows read left→right (not serpentine): the LR source/target handles point
 * right, so alternating direction would reverse the handles and confuse the
 * reading order. The single "return" edge wrapping to the next row routes via
 * React Flow's default edge, which is acceptable and far more readable than
 * one very wide row.
 */
function wrapColumns(
  nodes: Node[],
  nodesep: number,
  maxPerRow: number,
): Node[] {
  // Group by rounded x (dagre column). Rounding absorbs sub-pixel drift.
  const columns = new Map<number, Node[]>();
  for (const node of nodes) {
    const key = Math.round(node.position.x);
    const bucket = columns.get(key);
    if (bucket) bucket.push(node);
    else columns.set(key, [node]);
  }

  const orderedKeys = [...columns.keys()].sort((a, b) => a - b);

  // Tallest column extent (bottom − top of its nodes) drives ROW_STEP so the
  // largest branch fan-out still clears the next row.
  let tallestExtent = NODE_HEIGHT;
  for (const key of orderedKeys) {
    const col = columns.get(key)!;
    const top = Math.min(...col.map((n) => n.position.y));
    const bottom = Math.max(...col.map((n) => n.position.y + NODE_HEIGHT));
    tallestExtent = Math.max(tallestExtent, bottom - top);
  }

  const COLUMN_STEP = NODE_WIDTH + nodesep;
  const ROW_GAP = NODE_HEIGHT * 2; // vertical breathing room between rows
  const ROW_STEP = tallestExtent + ROW_GAP;

  const repositioned = new Map<string, Node>();
  orderedKeys.forEach((key, ci) => {
    const col = columns.get(key)!;
    const rowIdx = Math.floor(ci / maxPerRow);
    const colInRow = ci % maxPerRow;
    const cellX = colInRow * COLUMN_STEP;
    const cellY = rowIdx * ROW_STEP;
    const columnMinY = Math.min(...col.map((n) => n.position.y));

    for (const node of col) {
      repositioned.set(node.id, {
        ...node,
        position: {
          x: cellX,
          // Preserve the node's vertical offset within its column so
          // parallel/branch nodes stay stacked exactly as dagre arranged them.
          y: cellY + (node.position.y - columnMinY),
        },
      });
    }
  });

  // Preserve original node order in the returned array.
  return nodes.map((n) => repositioned.get(n.id) ?? n);
}
