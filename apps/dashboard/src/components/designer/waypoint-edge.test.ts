import { Position } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import {
  CORNER_RADIUS,
  normalBend,
  nudgeWaypoint,
  orthogonalVertices,
  roundedPath,
  routePoints,
  waypointPath,
} from "./waypoint-edge";
import { designerNodeBox, handlePoint, layoutDesignerGraph } from "@/lib/graph-layout";

describe("orthogonalVertices", () => {
  it("returns an empty list without points", () => {
    expect(orthogonalVertices([])).toEqual([]);
  });

  it("expands a diagonal segment to H→V→H through mid-x", () => {
    expect(
      orthogonalVertices([
        { x: 0, y: 0 },
        { x: 100, y: 50 },
      ]),
    ).toEqual([
      { x: 0, y: 0 },
      { x: 50, y: 0 },
      { x: 50, y: 50 },
      { x: 100, y: 50 },
    ]);
  });

  it("keeps horizontal/vertical segments straight, only bends diagonals", () => {
    expect(
      orthogonalVertices([
        { x: 0, y: 0 },
        { x: 40, y: 10 },
        { x: 80, y: 10 },
        { x: 120, y: 0 },
      ]),
    ).toEqual([
      { x: 0, y: 0 },
      { x: 20, y: 0 },
      { x: 20, y: 10 },
      { x: 40, y: 10 },
      { x: 80, y: 10 },
      { x: 100, y: 10 },
      { x: 100, y: 0 },
      { x: 120, y: 0 },
    ]);
  });
});

describe("roundedPath", () => {
  it("renders an empty path without vertices", () => {
    expect(roundedPath([])).toBe("");
  });

  it("keeps a straight line straight (no curve)", () => {
    const d = roundedPath([
      { x: 0, y: 0 },
      { x: 0, y: 100 },
    ]);
    expect(d).toBe("M 0,0 L 0,100");
    expect(d).not.toContain("Q");
  });

  it("leaves collinear vertices straight", () => {
    expect(
      roundedPath([
        { x: 0, y: 0 },
        { x: 5, y: 0 },
        { x: 10, y: 0 },
      ]),
    ).toBe("M 0,0 L 5,0 L 10,0");
  });

  it("rounds a right-angle corner with a quadratic through the sharp vertex", () => {
    expect(
      roundedPath(
        [
          { x: 0, y: 0 },
          { x: 50, y: 0 },
          { x: 50, y: 50 },
        ],
        10,
      ),
    ).toBe("M 0,0 L 40,0 Q 50,0 50,10 L 50,50");
  });

  it("clamps the radius to half the shorter adjacent leg", () => {
    expect(
      roundedPath(
        [
          { x: 0, y: 0 },
          { x: 6, y: 0 },
          { x: 6, y: 6 },
        ],
        10,
      ),
    ).toBe("M 0,0 L 3,0 Q 6,0 6,3 L 6,6");
  });
});

describe("waypointPath", () => {
  it("renders an empty path without points", () => {
    expect(waypointPath([])).toBe("");
  });

  it("rounds the corners of an orthogonalised diagonal", () => {
    expect(
      waypointPath([
        { x: 0, y: 0 },
        { x: 100, y: 50 },
      ]),
    ).toBe("M 0,0 L 40,0 Q 50,0 50,10 L 50,40 Q 50,50 60,50 L 100,50");
  });

  it("uses CORNER_RADIUS by default", () => {
    expect(CORNER_RADIUS).toBeGreaterThan(0);
    // A straight vertical run has no corner to round.
    expect(waypointPath([{ x: 0, y: 0 }, { x: 0, y: 200 }])).not.toContain("Q");
  });
});

describe("normalBend", () => {
  it("aligns the leg vertically for Top/Bottom handles (shares anchor x)", () => {
    const anchor = { x: 100, y: 200 };
    const neighbor = { x: 40, y: 60 };
    expect(normalBend(anchor, Position.Top, neighbor)).toEqual({ x: 100, y: 60 });
    expect(normalBend(anchor, Position.Bottom, neighbor)).toEqual({ x: 100, y: 60 });
  });

  it("aligns the leg horizontally for Left/Right handles (shares anchor y)", () => {
    const anchor = { x: 100, y: 200 };
    const neighbor = { x: 40, y: 60 };
    expect(normalBend(anchor, Position.Left, neighbor)).toEqual({ x: 40, y: 200 });
    expect(normalBend(anchor, Position.Right, neighbor)).toEqual({ x: 40, y: 200 });
  });

  it("returns the anchor for an unknown position", () => {
    expect(normalBend({ x: 5, y: 6 }, undefined, { x: 9, y: 9 })).toEqual({ x: 5, y: 6 });
  });
});

describe("routePoints", () => {
  it("makes the final leg a full straight run into the target arrow (no perpendicular nub)", () => {
    // A waypoint at (0,100) drops vertically from the source; the leg into a Top
    // handle must be vertical AND span the whole gap, so the arrowhead continues
    // a long straight line (──►) rather than a tiny stub (──^).
    const points = routePoints(
      { x: 0, y: 0 },
      Position.Bottom,
      [{ x: 0, y: 100 }],
      { x: 60, y: 120 },
      Position.Top,
    );
    const last = points[points.length - 1];
    const beforeLast = points[points.length - 2];
    expect(last).toEqual({ x: 60, y: 120 });
    // Bend sits at the target's x, at the waypoint's y → final leg is vertical
    // and 20 units long (120 - 100), not an 18px nub.
    expect(beforeLast).toEqual({ x: 60, y: 100 });
    expect(beforeLast.x).toBe(last.x);
  });

  it("leaves the source straight along its normal", () => {
    const points = routePoints(
      { x: 0, y: 0 },
      Position.Bottom,
      [{ x: 40, y: 60 }],
      { x: 60, y: 120 },
      Position.Top,
    );
    // First leg shares the source's x → vertical exit down to the waypoint's y.
    expect(points[0]).toEqual({ x: 0, y: 0 });
    expect(points[1]).toEqual({ x: 0, y: 60 });
    expect(points[1].x).toBe(points[0].x);
  });

  it("collapses to a straight line for unknown handle positions", () => {
    const points = routePoints(
      { x: 0, y: 0 },
      undefined,
      [],
      { x: 100, y: 50 },
      undefined,
    );
    expect(points).toEqual([
      { x: 0, y: 0 },
      { x: 100, y: 50 },
    ]);
  });

  it("does not cross when there are no waypoints (aligned handles → straight)", () => {
    const points = routePoints(
      { x: 50, y: 0 },
      Position.Bottom,
      [],
      { x: 50, y: 200 },
      Position.Top,
    );
    // Same x, vertical flow → a single straight vertical segment, no detours.
    expect(points).toEqual([
      { x: 50, y: 0 },
      { x: 50, y: 200 },
    ]);
  });
});

describe("nudgeWaypoint", () => {
  it("moves a waypoint by keyboard direction", () => {
    expect(nudgeWaypoint({ x: 10, y: 20 }, "ArrowUp")).toEqual({ x: 10, y: 8 });
    expect(nudgeWaypoint({ x: 10, y: 20 }, "ArrowRight", 5)).toEqual({
      x: 15,
      y: 20,
    });
  });
});

describe("routes after a designer layout (#1033)", () => {
  // Real drawn route: the edge's handles on the sides the layout picked
  // (round 3), the layout's waypoints through ``routePoints``, expanded the
  // way ``waypointPath`` draws it; every segment must stay out of every box.
  function drawnRouteCrossesANode(
    nodes: Parameters<typeof layoutDesignerGraph>[0],
    edges: Parameters<typeof layoutDesignerGraph>[1],
    withWaypoints = true,
  ): Record<string, boolean> {
    const { nodes: laidOut, waypoints, sides } = layoutDesignerGraph(nodes, edges);
    const boxes = laidOut.map(designerNodeBox);
    const byId = new Map(boxes.map((b) => [b.id, b]));
    const out: Record<string, boolean> = {};
    for (const edge of edges) {
      const ss = sides[edge.source].source;
      const ts = sides[edge.target].target;
      const points = routePoints(
        handlePoint(byId.get(edge.source)!, ss),
        ss,
        withWaypoints ? waypoints[edge.id] : [],
        handlePoint(byId.get(edge.target)!, ts),
        ts,
      );
      const vertices = orthogonalVertices(points);
      out[edge.id] = vertices.slice(1).some((b, i) => {
        const a = vertices[i];
        return boxes.some(
          (box) =>
            Math.min(a.x, b.x) < box.x + box.width &&
            Math.max(a.x, b.x) > box.x &&
            Math.min(a.y, b.y) < box.y + box.height &&
            Math.max(a.y, b.y) > box.y,
        );
      });
    }
    return out;
  }

  const node = (id: string) => ({
    id,
    type: "default",
    position: { x: 0, y: 0 },
    data: { label: id },
  });
  const edge = (id: string, source: string, target: string) => ({ id, source, target });

  it("a back-edge's route crosses no node box", () => {
    const nodes = ["plan", "code", "test", "review"].map(node);
    const edges = [
      edge("e1", "plan", "code"),
      edge("e2", "code", "test"),
      edge("e3", "test", "review"),
      edge("back", "review", "code"),
      edge("loop", "test", "plan"),
    ];
    const crosses = drawnRouteCrossesANode(nodes, edges);
    expect(crosses).toEqual({ e1: false, e2: false, e3: false, back: false, loop: false });
    // The check has teeth: drawn without the layout's waypoints, the same
    // back-edges cut through nodes.
    const bare = drawnRouteCrossesANode(nodes, edges, false);
    expect(bare.back || bare.loop).toBe(true);
  });

  it("a conditional forward edge skipping columns routes around the nodes it passes", () => {
    const nodes = ["a", "b", "c", "d"].map(node);
    const edges = [
      edge("ab", "a", "b"),
      edge("bc", "b", "c"),
      edge("cd", "c", "d"),
      edge("skip", "a", "d"),
    ];
    const crosses = drawnRouteCrossesANode(nodes, edges);
    expect(crosses.skip).toBe(false);
  });

  const chainIds = (n: number) => Array.from({ length: n }, (_, i) => `n${i}`);

  it("a snake-wrapped chain with loops and skips across rows crosses no node box", () => {
    const ids = chainIds(26);
    const edges = ids.slice(1).map((id, i) => edge(`e${i}`, ids[i], id));
    edges.push(
      edge("loop-row", "n5", "n2"), // back within the first row
      edge("loop-up", "n20", "n3"), // back across rows
      edge("skip-down", "n4", "n17"), // forward across rows
      edge("self", "n9", "n9"),
    );
    const crosses = drawnRouteCrossesANode(ids.map(node), edges);
    expect(Object.entries(crosses).filter(([, c]) => c)).toEqual([]);
  });

  it("a fan-out of 5 branches at a row turn crosses no node box", () => {
    const ids = chainIds(8);
    const edges = ids.slice(1).map((id, i) => edge(`e${i}`, ids[i], id));
    for (let b = 0; b < 5; b++) {
      for (let d = 0; d < 5; d++) {
        ids.push(`b${b}_${d}`);
        edges.push(edge(`f${b}_${d}`, d === 0 ? "n7" : `b${b}_${d - 1}`, `b${b}_${d}`));
      }
      edges.push(edge(`j${b}`, `b${b}_4`, "join"));
    }
    ids.push("join");
    const crosses = drawnRouteCrossesANode(ids.map(node), edges);
    expect(Object.entries(crosses).filter(([, c]) => c)).toEqual([]);
  });
});
