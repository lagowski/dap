import { describe, expect, it } from "vitest";

import {
  parseEdgeWaypoints,
  summarizeCondition,
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
  it("hydrates waypoint edge data with an arrowhead", () => {
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
    // Direction is shown with an arrowhead (#765).
    expect(edge.markerEnd).toBeDefined();
    expect(edge.label).toBeUndefined(); // no condition → no label
  });

  it("labels a conditional edge with the condition, not a bare 'if'", () => {
    const edge = toReactFlowEdge({
      id: "e2",
      source: "code-reviewer",
      target: "coder",
      condition: {
        type: "comparison",
        field: "extensions.review_approved",
        operator: "==",
        value: false,
      },
    });
    expect(edge.label).toBe("review_approved == false");
    expect(edge.animated).toBe(true);
  });
});

describe("summarizeCondition", () => {
  it("renders a comparison and strips the extensions. prefix", () => {
    expect(
      summarizeCondition({
        type: "comparison",
        field: "extensions.attempts",
        operator: "<",
        value: 2,
      }),
    ).toBe("attempts < 2");
  });

  it("joins logical children with AND/OR and truncates past two", () => {
    expect(
      summarizeCondition({
        type: "and",
        children: [
          { type: "comparison", field: "a", operator: "==", value: true },
          { type: "comparison", field: "b", operator: ">", value: 1 },
          { type: "comparison", field: "c", operator: "<", value: 5 },
        ],
      }),
    ).toBe("a == true AND b > 1 …");
  });
});
