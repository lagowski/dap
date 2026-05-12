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
});
