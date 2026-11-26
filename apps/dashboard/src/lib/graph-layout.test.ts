import { describe, it, expect } from "vitest";
import type { Node, Edge } from "@xyflow/react";
import {
  arrangeDesignerGraph,
  boxesOverlap,
  designerNodeBox,
  layoutDesignerGraph,
  needsDesignerLayout,
  restoreArrangeSnapshot,
  getLayoutedElements,
  fitViewportToBoxes,
  measuredNodeBox,
  savedViewportNeedsFit,
  visibleBoxFraction,
  FIT_MAX_ZOOM,
  type FlowViewport,
  type NodeBox,
  type PaneSize,
} from "./graph-layout";

const makeNode = (id: string): Node => ({
  id,
  type: "default",
  position: { x: 0, y: 0 },
  data: { label: id },
});

const makeEdge = (id: string, source: string, target: string): Edge => ({
  id,
  source,
  target,
});

describe("getLayoutedElements", () => {
  it("assigns non-zero positions to all nodes in a linear chain", () => {
    const nodes = [makeNode("a"), makeNode("b"), makeNode("c")];
    const edges = [makeEdge("e1", "a", "b"), makeEdge("e2", "b", "c")];
    const { nodes: layouted } = getLayoutedElements(nodes, edges);

    expect(layouted).toHaveLength(3);
    for (const node of layouted) {
      // Dagre assigns distinct x positions in LR layout
      expect(typeof node.position.x).toBe("number");
      expect(typeof node.position.y).toBe("number");
    }
    // LR: nodes should be spread horizontally
    const xs = layouted.map((n) => n.position.x).sort((a, b) => a - b);
    expect(xs[0]).toBeLessThan(xs[1]);
    expect(xs[1]).toBeLessThan(xs[2]);
  });

  it("returns the same edge array (positions not mutated)", () => {
    const nodes = [makeNode("x"), makeNode("y")];
    const edges = [makeEdge("e1", "x", "y")];
    const { edges: result } = getLayoutedElements(nodes, edges);
    expect(result).toBe(edges); // same reference — edges untouched
  });

  it("handles a single node with no edges", () => {
    const nodes = [makeNode("solo")];
    const { nodes: layouted } = getLayoutedElements(nodes, []);
    expect(layouted).toHaveLength(1);
    expect(typeof layouted[0].position.x).toBe("number");
  });

  it("respects custom nodesep / ranksep options", () => {
    const nodes = [makeNode("a"), makeNode("b")];
    const edges = [makeEdge("e1", "a", "b")];
    const { nodes: tight } = getLayoutedElements(nodes, edges, {
      nodesep: 10,
      ranksep: 20,
    });
    const { nodes: wide } = getLayoutedElements(nodes, edges, {
      nodesep: 200,
      ranksep: 400,
    });
    // Wide layout must spread nodes further apart
    const tightDist = Math.abs(tight[1].position.x - tight[0].position.x);
    const wideDist = Math.abs(wide[1].position.x - wide[0].position.x);
    expect(wideDist).toBeGreaterThan(tightDist);
  });

  describe("row wrapping (maxPerRow)", () => {
    const linearChain = (n: number): { nodes: Node[]; edges: Edge[] } => {
      const nodes = Array.from({ length: n }, (_, i) => makeNode(`n${i}`));
      const edges = Array.from({ length: n - 1 }, (_, i) =>
        makeEdge(`e${i}`, `n${i}`, `n${i + 1}`),
      );
      return { nodes, edges };
    };

    it("is backward-compatible: unset maxPerRow keeps a single row", () => {
      const { nodes, edges } = linearChain(8);
      const { nodes: layouted } = getLayoutedElements(nodes, edges);
      // Single LR row: every node shares the same y.
      const ys = new Set(layouted.map((n) => Math.round(n.position.y)));
      expect(ys.size).toBe(1);
    });

    it("is backward-compatible: maxPerRow=0 keeps a single row", () => {
      const { nodes, edges } = linearChain(8);
      const { nodes: layouted } = getLayoutedElements(nodes, edges, {
        maxPerRow: 0,
      });
      const ys = new Set(layouted.map((n) => Math.round(n.position.y)));
      expect(ys.size).toBe(1);
    });

    it("wraps a linear chain of N nodes into ceil(N/maxPerRow) rows", () => {
      const N = 13;
      const maxPerRow = 5;
      const { nodes, edges } = linearChain(N);
      const { nodes: layouted } = getLayoutedElements(nodes, edges, {
        maxPerRow,
      });

      const byId = new Map(layouted.map((n) => [n.id, n.position]));
      // Expect 3 distinct rows for 13 nodes at 5 per row.
      const rowYs = [
        ...new Set([...byId.values()].map((p) => Math.round(p.y))),
      ].sort((a, b) => a - b);
      expect(rowYs).toHaveLength(Math.ceil(N / maxPerRow));

      // Row k holds columns [k*maxPerRow, ...] in left->right order.
      for (let i = 0; i < N; i++) {
        const pos = byId.get(`n${i}`)!;
        const expectedRow = Math.floor(i / maxPerRow);
        expect(Math.round(pos.y)).toBe(rowYs[expectedRow]);
      }

      // Within each row, x increases left->right with column index.
      for (let row = 0; row < rowYs.length; row++) {
        const start = row * maxPerRow;
        const end = Math.min(start + maxPerRow, N);
        let prevX = -Infinity;
        for (let i = start; i < end; i++) {
          const x = byId.get(`n${i}`)!.x;
          expect(x).toBeGreaterThan(prevX);
          prevX = x;
        }
      }
    });

    it("does not overlap wrapped rows vertically", () => {
      const N = 12;
      const maxPerRow = 5;
      const { nodes, edges } = linearChain(N);
      const { nodes: layouted } = getLayoutedElements(nodes, edges, {
        maxPerRow,
      });
      const rowYs = [
        ...new Set(layouted.map((n) => Math.round(n.position.y))),
      ].sort((a, b) => a - b);
      // Consecutive rows must be separated by at least one node height.
      for (let i = 1; i < rowYs.length; i++) {
        expect(rowYs[i] - rowYs[i - 1]).toBeGreaterThan(40);
      }
    });

    it("keeps branch nodes (shared rank) stacked in the same column", () => {
      // root -> b1, root -> b2 (b1,b2 share a rank/column), then both -> join
      const nodes = [
        makeNode("root"),
        makeNode("b1"),
        makeNode("b2"),
        makeNode("join"),
      ];
      const edges = [
        makeEdge("e1", "root", "b1"),
        makeEdge("e2", "root", "b2"),
        makeEdge("e3", "b1", "join"),
        makeEdge("e4", "b2", "join"),
      ];
      const { nodes: layouted } = getLayoutedElements(nodes, edges, {
        maxPerRow: 5,
      });
      const byId = new Map(layouted.map((n) => [n.id, n.position]));
      // Branch nodes share the same column → same x, different y, same row.
      expect(Math.round(byId.get("b1")!.x)).toBe(
        Math.round(byId.get("b2")!.x),
      );
      expect(Math.round(byId.get("b1")!.y)).not.toBe(
        Math.round(byId.get("b2")!.y),
      );
    });

    it("returns the same edge array when wrapping", () => {
      const { nodes, edges } = linearChain(8);
      const { edges: result } = getLayoutedElements(nodes, edges, {
        maxPerRow: 5,
      });
      expect(result).toBe(edges);
    });
  });
});

describe("designer layout guard (#1033)", () => {
  const placed = (id: string, x: number, y: number): Node => ({
    id,
    type: "default",
    position: { x, y },
    data: { label: id },
  });
  const anyOverlap = (nodes: Node[]) => {
    const boxes = nodes.map(designerNodeBox);
    return boxes.some((a, i) => boxes.slice(i + 1).some((b) => boxesOverlap(a, b)));
  };

  it("a bundle with every node at y=0 and x 100 px apart has no overlapping box after the guard", () => {
    const nodes = ["a", "b", "c", "d", "e"].map((id, i) => placed(id, i * 100, 0));
    const edges = [
      makeEdge("e1", "a", "b"),
      makeEdge("e2", "b", "c"),
      makeEdge("e3", "b", "d"),
      makeEdge("e4", "c", "e"),
      makeEdge("e5", "d", "e"),
    ];
    expect(anyOverlap(nodes)).toBe(true);
    expect(needsDesignerLayout(nodes)).toBe(true);

    const { nodes: laidOut } = layoutDesignerGraph(nodes, edges);

    expect(anyOverlap(laidOut)).toBe(false);
    expect(needsDesignerLayout(laidOut)).toBe(false);
  });

  it("a node without a position triggers the layout", () => {
    const missing = { ...placed("b", 0, 0), position: undefined } as unknown as Node;
    expect(needsDesignerLayout([placed("a", 0, 0), missing])).toBe(true);
  });

  it("a graph without overlaps keeps its positions", () => {
    const nodes = [placed("a", 0, 0), placed("b", 400, 0), placed("c", 0, 300)];
    expect(needsDesignerLayout(nodes)).toBe(false);
  });

  it("Auto-arrange followed by undo restores the positions and waypoints", () => {
    const nodes = [placed("a", 0, 0), placed("b", 400, 0), placed("c", 0, 300)];
    const edges: Edge[] = [
      { ...makeEdge("e1", "a", "b"), data: { waypoints: [{ x: 7, y: 9 }] } },
      { ...makeEdge("e2", "b", "c"), data: { waypoints: [] } },
      { ...makeEdge("e3", "c", "a"), data: { waypoints: [] } },
    ];

    const arranged = arrangeDesignerGraph(nodes, edges, new Set(["e2"]));
    expect(arranged.nodes.map((n) => n.position)).not.toEqual(nodes.map((n) => n.position));

    const restored = restoreArrangeSnapshot(arranged.nodes, arranged.edges, arranged.snapshot);
    expect(restored.nodes.map((n) => n.position)).toEqual(nodes.map((n) => n.position));
    expect(restored.edges.map((e) => (e.data as { waypoints: unknown }).waypoints)).toEqual([
      [{ x: 7, y: 9 }],
      [],
      [],
    ]);
  });
});

// #1033 follow-up: the arranged graph must be framed by the view.
function screenBox(box: NodeBox, vp: FlowViewport) {
  return {
    left: box.x * vp.zoom + vp.x,
    top: box.y * vp.zoom + vp.y,
    right: (box.x + box.width) * vp.zoom + vp.x,
    bottom: (box.y + box.height) * vp.zoom + vp.y,
  };
}

function expectAllInside(boxes: NodeBox[], vp: FlowViewport, pane: PaneSize) {
  for (const box of boxes) {
    const s = screenBox(box, vp);
    expect(s.left, `${box.id} left`).toBeGreaterThanOrEqual(0);
    expect(s.top, `${box.id} top`).toBeGreaterThanOrEqual(0);
    expect(s.right, `${box.id} right`).toBeLessThanOrEqual(pane.width);
    expect(s.bottom, `${box.id} bottom`).toBeLessThanOrEqual(pane.height);
  }
}

describe("framing the arranged graph (#1033 follow-up)", () => {
  const chain = (n: number) => {
    const ids = Array.from({ length: n }, (_, i) => `n${i}`);
    return {
      nodes: ids.map(makeNode),
      edges: ids.slice(1).map((id, i) => makeEdge(`e${i}`, ids[i], id)),
    };
  };

  it.each([
    ["receipt-ocr (4 nodes)", 4, { width: 900, height: 570 }],
    ["issue-implementer (26 nodes)", 26, { width: 1100, height: 570 }],
  ])(
    "after the guard re-lays out a stacked %s bundle, every node box is inside the visible pane",
    (_label, count, pane) => {
      const { nodes, edges } = chain(count);
      expect(needsDesignerLayout(nodes)).toBe(true);
      const arranged = arrangeDesignerGraph(nodes, edges);
      const boxes = arranged.nodes.map(measuredNodeBox);
      const vp = fitViewportToBoxes(boxes, pane);
      expect(vp).not.toBeNull();
      expectAllInside(boxes, vp!, pane);
      expect(vp!.zoom).toBeLessThanOrEqual(FIT_MAX_ZOOM);
      expect(visibleBoxFraction(vp!, pane, boxes)).toBe(1);
    },
  );

  it("the stale zoom-1.72 viewport crops a re-laid-out receipt-ocr graph and needs a fit", () => {
    const { nodes, edges } = chain(4);
    const boxes = arrangeDesignerGraph(nodes, edges).nodes.map(measuredNodeBox);
    const pane = { width: 900, height: 570 };
    const stale = { x: 0, y: 0, zoom: 1.72 };
    expect(visibleBoxFraction(stale, pane, boxes)).toBeLessThan(1);
    // A viewport framing no node at all needs a fit.
    expect(savedViewportNeedsFit({ x: -50_000, y: 0, zoom: 1 }, pane, boxes)).toBe(true);
  });

  it("keeps a saved viewport that shows most of the graph", () => {
    const { nodes, edges } = chain(4);
    const boxes = arrangeDesignerGraph(nodes, edges).nodes.map(measuredNodeBox);
    const pane = { width: 900, height: 570 };
    const fitted = fitViewportToBoxes(boxes, pane)!;
    expect(savedViewportNeedsFit(fitted, pane, boxes)).toBe(false);
  });

  it("asks for a fit when more than half the graph is cropped", () => {
    const boxes: NodeBox[] = [0, 1, 2, 3].map((i) => ({
      id: `b${i}`,
      x: i * 200,
      y: 0,
      width: 100,
      height: 40,
    }));
    const pane = { width: 250, height: 200 };
    // Zoom 1 shows flow x 0..250: b0 and half of b1, 1.5 of 4 boxes.
    expect(savedViewportNeedsFit({ x: 0, y: 0, zoom: 1 }, pane, boxes)).toBe(true);
    // Zoom 0.5 shows flow x 0..500: b0..b2, 3 of 4 boxes.
    expect(savedViewportNeedsFit({ x: 0, y: 0, zoom: 0.5 }, pane, boxes)).toBe(false);
  });

  it("uses measured node sizes when React Flow has them", () => {
    const box = measuredNodeBox({
      id: "a",
      position: { x: 1, y: 2 },
      measured: { width: 210, height: 64 },
    });
    expect(box).toEqual({ id: "a", x: 1, y: 2, width: 210, height: 64 });
  });

  it("returns null before the pane has a size or without nodes", () => {
    expect(fitViewportToBoxes([], { width: 800, height: 600 })).toBeNull();
    expect(
      fitViewportToBoxes([{ id: "a", x: 0, y: 0, width: 10, height: 10 }], { width: 0, height: 0 }),
    ).toBeNull();
  });
});
