import { describe, it, expect } from "vitest";
import type { Node, Edge } from "@xyflow/react";
import { getLayoutedElements } from "./graph-layout";

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
