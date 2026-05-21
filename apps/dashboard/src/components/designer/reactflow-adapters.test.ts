import { describe, expect, it } from "vitest";

import {
  parseEdgeWaypoints,
  toReactFlowEdge,
} from "./reactflow-adapters";

describe("parseEdgeWaypoints", () => {
  it("returns valid edge waypoint lists from ui_metadata", () => {
    expect(
      parseEdgeWaypoints({
        edge_waypoints: {
          e1: [
            { x: 10, y: 20 },
            { x: 30, y: 40 },
          ],
          empty: [],
          malformed: [{ x: "bad", y: 10 }],
        },
      }),
    ).toEqual({
      e1: [
        { x: 10, y: 20 },
        { x: 30, y: 40 },
      ],
    });
  });
});

describe("toReactFlowEdge", () => {
  it("hydrates waypoint edge data", () => {
    const edge = toReactFlowEdge(
      { id: "e1", source: "n1", target: "n2" },
      [{ x: 10, y: 20 }],
    );

    expect(edge).toMatchObject({
      id: "e1",
      source: "n1",
      target: "n2",
      type: "waypoint",
      data: { waypoints: [{ x: 10, y: 20 }] },
    });
  });
});
