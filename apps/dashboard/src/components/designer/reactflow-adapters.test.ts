import { describe, expect, it } from "vitest";

import type { PipelineNode } from "@/lib/api/types";

import {
  fromReactFlowNode,
  parseEdgeWaypoints,
  summarizeCondition,
  toReactFlowEdge,
  toReactFlowNode,
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

  it("prefixes a negated comparison with NOT (#930)", () => {
    expect(
      summarizeCondition({
        type: "not",
        children: [
          {
            type: "comparison",
            field: "extensions.approved",
            operator: "==",
            value: true,
          },
        ],
      }),
    ).toBe("NOT approved == true");
  });

  it("parenthesises a negated compound condition (#930)", () => {
    expect(
      summarizeCondition({
        type: "not",
        children: [
          {
            type: "or",
            children: [
              { type: "comparison", field: "a", operator: "==", value: true },
              { type: "comparison", field: "b", operator: ">", value: 1 },
            ],
          },
        ],
      }),
    ).toBe("NOT (a == true OR b > 1)");
  });
});

describe("toReactFlowNode / fromReactFlowNode", () => {
  const retry = {
    max_retries: 3,
    retry_to: "coder",
    on_failure: "__end__",
    feedback_into: "test_feedback",
    short_circuit_on: null,
  };

  it("round-trips a node's retry policy through React Flow data (#931)", () => {
    const node: PipelineNode = {
      id: "tester",
      agent_id: "a1",
      position: { x: 1, y: 2 },
      retry,
    };
    expect(fromReactFlowNode(toReactFlowNode(node))).toEqual(node);
  });

  it("omits retry for a node without one", () => {
    const node: PipelineNode = { id: "n1", agent_id: "a1", position: { x: 0, y: 0 } };
    const back = fromReactFlowNode(toReactFlowNode(node));
    expect(back).toEqual(node);
    expect("retry" in back).toBe(false);
  });
});
