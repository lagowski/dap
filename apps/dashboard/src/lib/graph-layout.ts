/**
 * Dagre auto-layout for pipeline graphs.
 *
 * ``getLayoutedElements`` lays out the read-only run detail view (#225).
 * The designer keeps manual node positions, except that a graph opened with
 * missing or overlapping positions, or an "Auto-arrange" click, runs
 * ``layoutDesignerGraph`` (#1033).
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

/* ------------------------------------------------------------------ */
/* Designer layout guard + auto-arrange (#1033)                        */
/* ------------------------------------------------------------------ */

/** Box of a designer node: React Flow's default node (150 px wide, ~40 px
 *  tall with its padding), not the run view's 160x40 placeholder. */
export const DESIGNER_NODE_WIDTH = 150;
export const DESIGNER_NODE_HEIGHT = 40;
const DESIGNER_NODESEP = 60;
const DESIGNER_RANKSEP = 120;
/** Distance an edge leg keeps from a node border. */
const ROUTE_MARGIN = 20;
/** Vertical gap between stacked back-edge lanes. */
const LANE_GAP = 24;

type Point = { x: number; y: number };
export type NodeBox = { id: string; x: number; y: number; width: number; height: number };
export type DesignerWaypoints = Record<string, Point[]>;

function finitePoint(value: unknown): value is Point {
  if (!value || typeof value !== "object") return false;
  const p = value as Partial<Point>;
  return (
    typeof p.x === "number" &&
    typeof p.y === "number" &&
    Number.isFinite(p.x) &&
    Number.isFinite(p.y)
  );
}

export function designerNodeBox(node: Pick<Node, "id" | "position">): NodeBox {
  return {
    id: node.id,
    x: node.position.x,
    y: node.position.y,
    width: DESIGNER_NODE_WIDTH,
    height: DESIGNER_NODE_HEIGHT,
  };
}

/** True when the two boxes share interior area (touching borders is fine). */
export function boxesOverlap(a: NodeBox, b: NodeBox): boolean {
  return (
    a.x < b.x + b.width &&
    b.x < a.x + a.width &&
    a.y < b.y + b.height &&
    b.y < a.y + a.height
  );
}

/** The guard: a graph needs a layout when any node lacks a usable position
 *  or any two designer node boxes overlap. */
export function needsDesignerLayout(nodes: Array<Pick<Node, "id" | "position">>): boolean {
  if (nodes.some((n) => !finitePoint(n.position))) return true;
  const boxes = nodes.map(designerNodeBox);
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      if (boxesOverlap(boxes[i], boxes[j])) return true;
    }
  }
  return false;
}

/** True when an axis-aligned segment passes through the interior of ``box``. */
function segmentCrossesBox(a: Point, b: Point, box: NodeBox): boolean {
  const minX = Math.min(a.x, b.x);
  const maxX = Math.max(a.x, b.x);
  const minY = Math.min(a.y, b.y);
  const maxY = Math.max(a.y, b.y);
  return (
    minX < box.x + box.width &&
    maxX > box.x &&
    minY < box.y + box.height &&
    maxY > box.y
  );
}

/** Expand a polyline into axis-aligned vertices (diagonals become H→V→H
 *  through the mid-x, the way ``WaypointEdge`` draws them). */
function orthogonal(points: Point[]): Point[] {
  const out: Point[] = points.length > 0 ? [points[0]] : [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (a.x === b.x || a.y === b.y) out.push(b);
    else {
      const midX = (a.x + b.x) / 2;
      out.push({ x: midX, y: a.y }, { x: midX, y: b.y }, b);
    }
  }
  return out;
}

/** True when the drawn route through ``points`` crosses any node box. */
export function routeCrossesBoxes(points: Point[], boxes: NodeBox[]): boolean {
  const vertices = orthogonal(points);
  for (let i = 1; i < vertices.length; i++) {
    for (const box of boxes) {
      if (segmentCrossesBox(vertices[i - 1], vertices[i], box)) return true;
    }
  }
  return false;
}

/** Designer handles: React Flow's default node takes edges in at the top
 *  centre and sends them out at the bottom centre. */
export function sourceHandle(box: NodeBox): Point {
  return { x: box.x + box.width / 2, y: box.y + box.height };
}
export function targetHandle(box: NodeBox): Point {
  return { x: box.x + box.width / 2, y: box.y };
}

/** The route a designer edge draws without waypoints: down out of the source
 *  handle's row, across, then into the target handle (same rule as
 *  ``routePoints`` for Bottom/Top handles). */
function defaultRoute(source: Point, target: Point): Point[] {
  return [source, { x: target.x, y: source.y }, target];
}

/**
 * Lay the designer graph out with dagre (LR, designer node size) and route
 * the edges that need it.
 *
 * Positions come from dagre. Each edge then gets waypoints when it is
 * conditional, a back-edge (its target sits in the same or an earlier
 * column, self-loops included) or when its plain route would cross a node.
 * Those routes leave the source downwards, turn in the gap between columns
 * (no node ever sits there), and, when source and target are not in adjacent
 * columns, run along a lane under every node they pass, stacking lanes so two
 * back-edges never share one. Plain edges that already clear every node keep
 * no waypoints.
 */
export function layoutDesignerGraph(
  nodes: Node[],
  edges: Edge[],
  conditionalEdgeIds: ReadonlySet<string> = new Set(),
): { nodes: Node[]; waypoints: DesignerWaypoints } {
  const g = new dagre.graphlib.Graph({ multigraph: true });
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: DESIGNER_NODESEP, ranksep: DESIGNER_RANKSEP });
  const ids = new Set(nodes.map((n) => n.id));
  for (const node of nodes) {
    g.setNode(node.id, { width: DESIGNER_NODE_WIDTH, height: DESIGNER_NODE_HEIGHT });
  }
  const routable = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  for (const edge of routable) g.setEdge(edge.source, edge.target, {}, edge.id);
  dagre.layout(g);

  const laidOut = nodes.map((node) => {
    const { x, y } = g.node(node.id);
    return {
      ...node,
      position: { x: x - DESIGNER_NODE_WIDTH / 2, y: y - DESIGNER_NODE_HEIGHT / 2 },
    };
  });
  const boxes = laidOut.map(designerNodeBox);
  const boxById = new Map(boxes.map((b) => [b.id, b]));
  const gap = DESIGNER_RANKSEP / 2;
  const lanes: Array<{ minX: number; maxX: number; y: number }> = [];

  const waypoints: DesignerWaypoints = {};
  for (const edge of routable) {
    const src = boxById.get(edge.source)!;
    const tgt = boxById.get(edge.target)!;
    const start = sourceHandle(src);
    const end = targetHandle(tgt);
    const isBack = tgt.x <= src.x;
    const isConditional = conditionalEdgeIds.has(edge.id);
    if (!isBack && !isConditional && !routeCrossesBoxes(defaultRoute(start, end), boxes)) {
      waypoints[edge.id] = [];
      continue;
    }
    const exitY = src.y + src.height + ROUTE_MARGIN;
    const entryY = tgt.y - ROUTE_MARGIN;
    const exitX = src.x + src.width + gap; // gap right of the source column
    const entryX = tgt.x - gap; // gap left of the target column
    const route: Point[] = [{ x: start.x, y: exitY }, { x: exitX, y: exitY }];
    if (exitX !== entryX) {
      const minX = Math.min(exitX, entryX);
      const maxX = Math.max(exitX, entryX);
      let laneY = Math.max(exitY, entryY);
      for (const box of boxes) {
        if (box.x < maxX && box.x + box.width > minX) {
          laneY = Math.max(laneY, box.y + box.height + ROUTE_MARGIN);
        }
      }
      for (const lane of lanes) {
        if (lane.minX < maxX && lane.maxX > minX) laneY = Math.max(laneY, lane.y + LANE_GAP);
      }
      lanes.push({ minX, maxX, y: laneY });
      route.push({ x: exitX, y: laneY }, { x: entryX, y: laneY });
    }
    route.push({ x: entryX, y: entryY }, { x: end.x, y: entryY });
    waypoints[edge.id] = route.filter(
      (p, i) => i === 0 || p.x !== route[i - 1].x || p.y !== route[i - 1].y,
    );
  }
  return { nodes: laidOut, waypoints };
}

/** What Auto-arrange replaced, so "Undo arrange" can put it back. */
export type ArrangeSnapshot = {
  positions: Record<string, Point>;
  waypoints: DesignerWaypoints;
};

function edgeWaypointsOf(edge: Edge): Point[] {
  const raw = (edge.data as { waypoints?: unknown } | undefined)?.waypoints;
  return Array.isArray(raw) ? raw.filter(finitePoint).map((p) => ({ x: p.x, y: p.y })) : [];
}

function withWaypoints(edge: Edge, waypoints: Point[]): Edge {
  return { ...edge, data: { ...(edge.data ?? {}), waypoints } };
}

/** Run the designer layout on React Flow state; returns the new state and a
 *  snapshot of the positions and waypoints it replaced. */
export function arrangeDesignerGraph(
  nodes: Node[],
  edges: Edge[],
  conditionalEdgeIds: ReadonlySet<string> = new Set(),
): { nodes: Node[]; edges: Edge[]; snapshot: ArrangeSnapshot } {
  const snapshot: ArrangeSnapshot = { positions: {}, waypoints: {} };
  for (const n of nodes) {
    if (finitePoint(n.position)) snapshot.positions[n.id] = { ...n.position };
  }
  for (const e of edges) snapshot.waypoints[e.id] = edgeWaypointsOf(e);
  const { nodes: laidOut, waypoints } = layoutDesignerGraph(nodes, edges, conditionalEdgeIds);
  return {
    nodes: laidOut,
    edges: edges.map((e) => (e.id in waypoints ? withWaypoints(e, waypoints[e.id]) : e)),
    snapshot,
  };
}

/** Undo an Auto-arrange: restore the snapshot's positions and waypoints.
 *  Nodes or edges added since keep their current state. */
export function restoreArrangeSnapshot(
  nodes: Node[],
  edges: Edge[],
  snapshot: ArrangeSnapshot,
): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: nodes.map((n) =>
      snapshot.positions[n.id] ? { ...n, position: { ...snapshot.positions[n.id] } } : n,
    ),
    edges: edges.map((e) =>
      snapshot.waypoints[e.id] ? withWaypoints(e, snapshot.waypoints[e.id]) : e,
    ),
  };
}

// --- Framing the view (#1033 follow-up) ----------------------------------

/** React Flow's viewport: a flow point p is drawn at ``p * zoom + (x, y)``. */
export type FlowViewport = { x: number; y: number; zoom: number };
export type PaneSize = { width: number; height: number };

/** Zoom bounds for a fitted view: never zoom a small graph past 1.5x, and
 *  stay inside the designer's saved-viewport range (0.1..4). */
export const FIT_MIN_ZOOM = 0.1;
export const FIT_MAX_ZOOM = 1.5;
/** Screen pixels left free around the graph in a fitted view. */
export const FIT_PADDING_PX = 40;

/** A node's box from its measured size (falls back to the designer size
 *  before React Flow has measured it). */
export function measuredNodeBox(
  node: Pick<Node, "id" | "position"> & {
    measured?: { width?: number; height?: number };
    width?: number;
    height?: number;
  },
): NodeBox {
  return {
    id: node.id,
    x: node.position.x,
    y: node.position.y,
    width: node.measured?.width ?? node.width ?? DESIGNER_NODE_WIDTH,
    height: node.measured?.height ?? node.height ?? DESIGNER_NODE_HEIGHT,
  };
}

function boxesBounds(boxes: NodeBox[]): NodeBox | null {
  if (boxes.length === 0) return null;
  const minX = Math.min(...boxes.map((b) => b.x));
  const minY = Math.min(...boxes.map((b) => b.y));
  const maxX = Math.max(...boxes.map((b) => b.x + b.width));
  const maxY = Math.max(...boxes.map((b) => b.y + b.height));
  return { id: "bounds", x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

/** The viewport that shows every box inside the pane, centred, with
 *  ``padding`` screen pixels around the graph. ``null`` without boxes or
 *  before the pane has a size. */
export function fitViewportToBoxes(
  boxes: NodeBox[],
  pane: PaneSize,
  padding: number = FIT_PADDING_PX,
): FlowViewport | null {
  const bounds = boxesBounds(boxes);
  if (bounds == null || pane.width <= 0 || pane.height <= 0) return null;
  const usableW = Math.max(pane.width - 2 * padding, 1);
  const usableH = Math.max(pane.height - 2 * padding, 1);
  const fitZoom = Math.min(
    usableW / Math.max(bounds.width, 1),
    usableH / Math.max(bounds.height, 1),
  );
  const zoom = Math.min(Math.max(fitZoom, FIT_MIN_ZOOM), FIT_MAX_ZOOM);
  const centreX = bounds.x + bounds.width / 2;
  const centreY = bounds.y + bounds.height / 2;
  return { x: pane.width / 2 - centreX * zoom, y: pane.height / 2 - centreY * zoom, zoom };
}

/** Fraction (0..1) of the boxes' total area that the viewport shows in the pane. */
export function visibleBoxFraction(
  viewport: FlowViewport,
  pane: PaneSize,
  boxes: NodeBox[],
): number {
  if (boxes.length === 0) return 1;
  // The pane's visible rectangle in flow coordinates.
  const view = {
    x: -viewport.x / viewport.zoom,
    y: -viewport.y / viewport.zoom,
    width: pane.width / viewport.zoom,
    height: pane.height / viewport.zoom,
  };
  let total = 0;
  let visible = 0;
  for (const b of boxes) {
    total += b.width * b.height;
    const w = Math.min(b.x + b.width, view.x + view.width) - Math.max(b.x, view.x);
    const h = Math.min(b.y + b.height, view.y + view.height) - Math.max(b.y, view.y);
    if (w > 0 && h > 0) visible += w * h;
  }
  return total > 0 ? visible / total : 1;
}

/** Open-time check on a saved viewport: fit the view instead when it shows
 *  no node or crops the graph by more than half. */
export function savedViewportNeedsFit(
  viewport: FlowViewport,
  pane: PaneSize,
  boxes: NodeBox[],
): boolean {
  if (boxes.length === 0 || pane.width <= 0 || pane.height <= 0) return false;
  return visibleBoxFraction(viewport, pane, boxes) < 0.5;
}
