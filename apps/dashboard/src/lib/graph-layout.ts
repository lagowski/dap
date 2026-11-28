/**
 * Dagre auto-layout for pipeline graphs.
 *
 * ``getLayoutedElements`` lays out the read-only run detail view (#225).
 * The designer keeps manual node positions, except that a graph opened with
 * missing or overlapping positions, or an "Auto-arrange" click, runs
 * ``layoutDesignerGraph`` (#1033).
 */
import dagre from "dagre";
import { Position, type Edge, type Node } from "@xyflow/react";

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

/* ---- Handle sides (#1033 round 3) ---------------------------------------
 * A designer node's handles sit on the side that faces its neighbours, worked
 * out from the node positions alone, so a graph reopened from its saved
 * positions draws exactly as Auto-arrange laid it out. A node with a
 * successor clearly to its right sends edges out on the right; at the turn of
 * a snake row, where the next node sits straight below with nothing between,
 * the edge leaves at the bottom and enters at the top. Back-edges (loops) do
 * not vote: they are routed around the nodes instead. */

/** Source (outgoing) and target (incoming) handle side of each node. */
export type HandleSides = Record<string, { source: Position; target: Position }>;

const OPPOSITE: Record<Position, Position> = {
  [Position.Top]: Position.Bottom,
  [Position.Bottom]: Position.Top,
  [Position.Left]: Position.Right,
  [Position.Right]: Position.Left,
};

function isVerticalSide(side: Position): boolean {
  return side === Position.Top || side === Position.Bottom;
}

/** The point on ``box`` where a handle on ``side`` sits (React Flow centres
 *  the default node's handles on their side). */
export function handlePoint(box: NodeBox, side: Position): Point {
  switch (side) {
    case Position.Top:
      return { x: box.x + box.width / 2, y: box.y };
    case Position.Bottom:
      return { x: box.x + box.width / 2, y: box.y + box.height };
    case Position.Left:
      return { x: box.x, y: box.y + box.height / 2 };
    default:
      return { x: box.x + box.width, y: box.y + box.height / 2 };
  }
}

/** ``point`` moved ``distance`` away from its node along the handle normal. */
function stepOut(point: Point, side: Position, distance: number): Point {
  switch (side) {
    case Position.Top:
      return { x: point.x, y: point.y - distance };
    case Position.Bottom:
      return { x: point.x, y: point.y + distance };
    case Position.Left:
      return { x: point.x - distance, y: point.y };
    default:
      return { x: point.x + distance, y: point.y };
  }
}

type EdgeLike = Pick<Edge, "id" | "source" | "target">;

/** Edges that close a cycle (their target is still on the depth-first stack
 *  when they are walked), self-loops included. Walks start at nodes without
 *  incoming edges, in node order, so the result does not depend on layout. */
export function backEdgeIds(nodeIds: string[], edges: EdgeLike[]): Set<string> {
  const ids = new Set(nodeIds);
  const outgoing = new Map<string, EdgeLike[]>(nodeIds.map((id) => [id, []]));
  const incoming = new Map<string, number>(nodeIds.map((id) => [id, 0]));
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue;
    outgoing.get(e.source)!.push(e);
    if (e.source !== e.target) incoming.set(e.target, incoming.get(e.target)! + 1);
  }
  const state = new Map<string, "open" | "done">();
  const back = new Set<string>();
  const visit = (id: string) => {
    state.set(id, "open");
    for (const e of outgoing.get(id)!) {
      const next = state.get(e.target);
      if (next === "open") back.add(e.id);
      else if (next == null) visit(e.target);
    }
    state.set(id, "done");
  };
  const roots = [
    ...nodeIds.filter((id) => incoming.get(id) === 0),
    ...nodeIds.filter((id) => incoming.get(id) !== 0),
  ];
  for (const id of roots) if (!state.has(id)) visit(id);
  return back;
}

/** No node box lies between ``a`` and ``b`` along the vertical line(s)
 *  through their centres. */
function verticalPathClear(a: NodeBox, b: NodeBox, boxes: NodeBox[]): boolean {
  const [upper, lower] = a.y <= b.y ? [a, b] : [b, a];
  const top = upper.y + upper.height;
  const bottom = lower.y;
  if (bottom <= top) return false;
  const minX = Math.min(a.x + a.width / 2, b.x + b.width / 2);
  const maxX = Math.max(a.x + a.width / 2, b.x + b.width / 2);
  return !boxes.some(
    (box) =>
      box.id !== a.id &&
      box.id !== b.id &&
      box.x <= maxX &&
      box.x + box.width >= minX &&
      box.y < bottom &&
      box.y + box.height > top,
  );
}

/** The side of ``from`` that faces ``to``, or null when no side clearly
 *  does (``to`` sits straight above or below behind another node). */
function facingSide(from: NodeBox, to: NodeBox, boxes: NodeBox[]): Position | null {
  const dx = to.x + to.width / 2 - (from.x + from.width / 2);
  const dy = to.y + to.height / 2 - (from.y + from.height / 2);
  if (Math.abs(dx) >= from.width / 2) return dx > 0 ? Position.Right : Position.Left;
  if (dy === 0 || !verticalPathClear(from, to, boxes)) return null;
  return dy > 0 ? Position.Bottom : Position.Top;
}

/** Most frequent side; a tie goes to the side voted first (edge order). */
function majoritySide(votes: Position[] | undefined): Position | null {
  if (!votes || votes.length === 0) return null;
  const counts = new Map<Position, number>();
  for (const v of votes) counts.set(v, (counts.get(v) ?? 0) + 1);
  let best = votes[0];
  for (const v of votes) if (counts.get(v)! > counts.get(best)!) best = v;
  return best;
}

/** Handle sides of every positioned node, from geometry only. */
export function designerHandleSides(
  nodes: Array<Pick<Node, "id" | "position">>,
  edges: EdgeLike[],
): HandleSides {
  const boxes = nodes.filter((n) => finitePoint(n.position)).map(designerNodeBox);
  const byId = new Map(boxes.map((b) => [b.id, b]));
  const back = backEdgeIds(
    boxes.map((b) => b.id),
    edges,
  );
  const sourceVotes = new Map<string, Position[]>();
  const targetVotes = new Map<string, Position[]>();
  const vote = (map: Map<string, Position[]>, id: string, side: Position | null) => {
    if (side == null) return;
    const list = map.get(id);
    if (list) list.push(side);
    else map.set(id, [side]);
  };
  for (const e of edges) {
    if (back.has(e.id) || e.source === e.target) continue;
    const s = byId.get(e.source);
    const t = byId.get(e.target);
    if (!s || !t) continue;
    vote(sourceVotes, s.id, facingSide(s, t, boxes));
    vote(targetVotes, t.id, facingSide(t, s, boxes));
  }
  const out: HandleSides = {};
  for (const box of boxes) {
    const source = majoritySide(sourceVotes.get(box.id));
    const target = majoritySide(targetVotes.get(box.id));
    out[box.id] = {
      source: source ?? (target ? OPPOSITE[target] : Position.Bottom),
      target: target ?? (source ? OPPOSITE[source] : Position.Top),
    };
  }
  return out;
}

/* ---- Snake layout (#1033 round 3) ------------------------------------- */

/** Horizontal gap between two node columns of a snake row. */
const COLUMN_GAP = 60;
const COLUMN_STEP = DESIGNER_NODE_WIDTH + COLUMN_GAP;
/** Smallest vertical gap between two snake rows. */
const ROW_GAP_MIN = 80;
/** A snake row holds about 6-8 columns (dagre ranks). */
export const SNAKE_MIN_COLUMNS = 6;
export const SNAKE_MAX_COLUMNS = 8;

/** Columns per snake row for a graph ``columns`` ranks deep: one row up to
 *  ``SNAKE_MAX_COLUMNS``, else rows as even as possible, at least
 *  ``SNAKE_MIN_COLUMNS`` wide. */
export function snakeColumnsPerRow(columns: number): number {
  if (columns <= SNAKE_MAX_COLUMNS) return Math.max(columns, 1);
  const rows = Math.ceil(columns / SNAKE_MAX_COLUMNS);
  return Math.max(SNAKE_MIN_COLUMNS, Math.ceil(columns / rows));
}

type Placement = { row: number; slot: number; offsetY: number };
type RoutePlan =
  | { kind: "straight" }
  | { kind: "mid" }
  | { kind: "lane"; gap: number; lane: number }
  | { kind: "corridor"; gapS: number; laneS: number; gapT: number; laneT: number; x: number };

/** Lanes of one gap between rows: each lane holds x-intervals that do not
 *  touch, so two routes never share a stretch of lane. */
function takeLane(lanes: Array<Array<[number, number]>>, a: number, b: number): number {
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  for (let k = 0; ; k++) {
    if (!lanes[k]) lanes[k] = [];
    if (lanes[k].every(([l, h]) => hi + 8 < l || h + 8 < lo)) {
      lanes[k].push([lo, hi]);
      return k;
    }
  }
}

/**
 * Lay the designer graph out as a snake and route the edges that need it.
 *
 * dagre (LR) gives every node a column (its depth) and a vertical order. The
 * columns are wrapped into rows of about 6-8 (``snakeColumnsPerRow``); even
 * rows run left to right, odd rows right to left, and a row's last column
 * sits straight above the next row's first, so a long chain reads as one
 * continuous line instead of one very wide row. Nodes of one column (a
 * fan-out) stay stacked inside the band of their own row.
 *
 * Handle sides come from ``designerHandleSides``. An edge between facing
 * handles of neighbouring columns, or across a turn, draws without
 * waypoints; any other edge (a loop back, a skip over columns or rows) runs
 * along a lane in the gap between rows and, between rows, up or down a gap
 * between columns, where no node ever sits. Gaps grow with their lanes.
 */
export function layoutDesignerGraph(
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; waypoints: DesignerWaypoints; sides: HandleSides } {
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

  // dagre columns, left to right.
  const centre = new Map(nodes.map((n) => [n.id, g.node(n.id) as Point]));
  const columnKeys = [...new Set(nodes.map((n) => Math.round(centre.get(n.id)!.x)))].sort(
    (a, b) => a - b,
  );
  const columnOf = new Map(columnKeys.map((key, i) => [key, i]));
  const perRow = snakeColumnsPerRow(columnKeys.length);
  const rowCount = Math.max(Math.ceil(columnKeys.length / perRow), 1);

  // Row, slot (left-to-right grid position) and offset inside the row band.
  const rowMinY = new Array<number>(rowCount).fill(Infinity);
  const rowOf = (id: string) =>
    Math.floor(columnOf.get(Math.round(centre.get(id)!.x))! / perRow);
  for (const n of nodes) {
    const r = rowOf(n.id);
    rowMinY[r] = Math.min(rowMinY[r], centre.get(n.id)!.y);
  }
  const placement = new Map<string, Placement>();
  const bandHeight = new Array<number>(rowCount).fill(DESIGNER_NODE_HEIGHT);
  for (const n of nodes) {
    const column = columnOf.get(Math.round(centre.get(n.id)!.x))!;
    const row = Math.floor(column / perRow);
    const j = column % perRow;
    const offsetY = centre.get(n.id)!.y - rowMinY[row];
    placement.set(n.id, { row, slot: row % 2 === 0 ? j : perRow - 1 - j, offsetY });
    bandHeight[row] = Math.max(bandHeight[row], offsetY + DESIGNER_NODE_HEIGHT);
  }

  // gap r sits above row r; gap rowCount below the last row.
  const place = (gapHeight: number[]) => {
    const gapTop: number[] = [];
    let y = 0;
    for (let r = 0; r <= rowCount; r++) {
      gapTop.push(y);
      y += gapHeight[r] + (r < rowCount ? bandHeight[r] : 0);
    }
    const laidOut = nodes.map((node) => {
      const p = placement.get(node.id)!;
      return {
        ...node,
        position: {
          x: p.slot * COLUMN_STEP,
          y: gapTop[p.row] + gapHeight[p.row] + p.offsetY,
        },
      };
    });
    return { laidOut, gapTop };
  };

  const baseGaps = Array.from({ length: rowCount + 1 }, (_, r) =>
    r === 0 || r === rowCount ? 0 : ROW_GAP_MIN,
  );
  const first = place(baseGaps);
  const sides = designerHandleSides(first.laidOut, routable);
  const firstBoxes = new Map(first.laidOut.map((n) => [n.id, designerNodeBox(n)]));
  const allFirstBoxes = [...firstBoxes.values()];

  // Plan every route; lanes depend on x only, which the gap heights never change.
  const lanes: Array<Array<Array<[number, number]>>> = baseGaps.map(() => []);
  const plans = new Map<string, RoutePlan>();
  for (const edge of routable) {
    const s = firstBoxes.get(edge.source)!;
    const t = firstBoxes.get(edge.target)!;
    const ps = placement.get(edge.source)!;
    const pt = placement.get(edge.target)!;
    const ss = sides[edge.source].source;
    const ts = sides[edge.target].target;
    const S = handlePoint(s, ss);
    const T = handlePoint(t, ts);
    const S1 = stepOut(S, ss, ROUTE_MARGIN);
    const T1 = stepOut(T, ts, ROUTE_MARGIN);
    const facing = edge.source !== edge.target && ts === OPPOSITE[ss];
    if (facing && !isVerticalSide(ss) && ps.row === pt.row) {
      const step = ss === Position.Right ? 1 : -1;
      if (pt.slot - ps.slot === step) {
        plans.set(edge.id, { kind: S.y === T.y ? "straight" : "mid" });
        continue;
      }
    }
    if (facing && isVerticalSide(ss) && S.x === T.x && !routeCrossesBoxes([S, T], allFirstBoxes)) {
      plans.set(edge.id, { kind: "straight" });
      continue;
    }
    let gapS = isVerticalSide(ss) ? (ss === Position.Bottom ? ps.row + 1 : ps.row) : null;
    let gapT = isVerticalSide(ts) ? (ts === Position.Top ? pt.row : pt.row + 1) : null;
    if (gapS == null && gapT == null) {
      gapS = gapT = pt.row > ps.row ? pt.row : pt.row < ps.row ? pt.row + 1 : ps.row + 1;
    } else if (gapS == null) gapS = gapT;
    else if (gapT == null) gapT = gapS;
    if (gapS === gapT) {
      plans.set(edge.id, { kind: "lane", gap: gapS!, lane: takeLane(lanes[gapS!], S1.x, T1.x) });
      continue;
    }
    // Change rows up or down the gap between columns nearest the middle.
    const mid = (S1.x + T1.x) / 2;
    let x = -COLUMN_GAP / 2;
    for (let k = 0; k <= perRow; k++) {
      const gx = k * COLUMN_STEP - COLUMN_GAP / 2;
      if (Math.abs(gx - mid) < Math.abs(x - mid)) x = gx;
    }
    plans.set(edge.id, {
      kind: "corridor",
      gapS: gapS!,
      laneS: takeLane(lanes[gapS!], S1.x, x),
      gapT: gapT!,
      laneT: takeLane(lanes[gapT!], x, T1.x),
      x,
    });
  }

  // Gaps tall enough for their lanes, then the final positions.
  const gapHeight = baseGaps.map((base, r) =>
    lanes[r].length > 0
      ? Math.max(base, 2 * ROUTE_MARGIN + LANE_GAP * lanes[r].length + 8)
      : base,
  );
  const { laidOut, gapTop } = place(gapHeight);
  const boxById = new Map(laidOut.map((n) => [n.id, designerNodeBox(n)]));
  const laneY = (gap: number, lane: number) => gapTop[gap] + ROUTE_MARGIN + 12 + LANE_GAP * lane;

  const waypoints: DesignerWaypoints = {};
  for (const edge of routable) {
    const plan = plans.get(edge.id)!;
    const ss = sides[edge.source].source;
    const ts = sides[edge.target].target;
    const S = handlePoint(boxById.get(edge.source)!, ss);
    const T = handlePoint(boxById.get(edge.target)!, ts);
    const S1 = stepOut(S, ss, ROUTE_MARGIN);
    const T1 = stepOut(T, ts, ROUTE_MARGIN);
    let route: Point[];
    if (plan.kind === "straight") route = [];
    else if (plan.kind === "mid") {
      const mx = (S.x + T.x) / 2;
      route = [
        { x: mx, y: S.y },
        { x: mx, y: T.y },
      ];
    } else if (plan.kind === "lane") {
      const y = laneY(plan.gap, plan.lane);
      route = [S1, { x: S1.x, y }, { x: T1.x, y }, T1];
    } else {
      const y1 = laneY(plan.gapS, plan.laneS);
      const y2 = laneY(plan.gapT, plan.laneT);
      route = [S1, { x: S1.x, y: y1 }, { x: plan.x, y: y1 }, { x: plan.x, y: y2 }, { x: T1.x, y: y2 }, T1];
    }
    waypoints[edge.id] = route.filter(
      (p, i) => i === 0 || p.x !== route[i - 1].x || p.y !== route[i - 1].y,
    );
  }
  return { nodes: laidOut, waypoints, sides };
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
): { nodes: Node[]; edges: Edge[]; snapshot: ArrangeSnapshot } {
  const snapshot: ArrangeSnapshot = { positions: {}, waypoints: {} };
  for (const n of nodes) {
    if (finitePoint(n.position)) snapshot.positions[n.id] = { ...n.position };
  }
  for (const e of edges) snapshot.waypoints[e.id] = edgeWaypointsOf(e);
  const { nodes: laidOut, waypoints } = layoutDesignerGraph(nodes, edges);
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

/** Smallest zoom at which a designer graph still reads (#1033 round 3). */
export const READABLE_ZOOM = 0.4;
/** The pane the open-time readability check assumes: the designer's canvas
 *  in a 1600x900 window (about 800x815 between the palette and the
 *  inspector), rounded down so the check errs towards arranging. */
export const REFERENCE_PANE: PaneSize = { width: 780, height: 760 };

/** Zoom at which ``boxes`` just fit ``pane`` (not clamped). */
export function fitZoomFor(
  boxes: NodeBox[],
  pane: PaneSize,
  padding: number = FIT_PADDING_PX,
): number {
  const bounds = boxesBounds(boxes);
  if (bounds == null) return Infinity;
  return Math.min(
    Math.max(pane.width - 2 * padding, 1) / Math.max(bounds.width, 1),
    Math.max(pane.height - 2 * padding, 1) / Math.max(bounds.height, 1),
  );
}

/** Open-time guard for a graph without overlaps: re-lay it out when it
 *  cannot be shown at ``READABLE_ZOOM`` (one very long row, say) and the
 *  arranged graph reads clearly better. */
export function arrangeImprovesReadability(
  current: NodeBox[],
  arranged: NodeBox[],
  pane: PaneSize = REFERENCE_PANE,
): boolean {
  const now = fitZoomFor(current, pane);
  if (now >= READABLE_ZOOM) return false;
  return fitZoomFor(arranged, pane) >= now * 1.25;
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
