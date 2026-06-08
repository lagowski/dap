import { Position } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { approachStub, nudgeWaypoint, routePoints, waypointPath } from "./waypoint-edge";

describe("waypointPath", () => {
  it("renders an empty path without points", () => {
    expect(waypointPath([])).toBe("");
  });

  it("renders a straight path with source and target", () => {
    expect(
      waypointPath([
        { x: 0, y: 0 },
        { x: 100, y: 50 },
      ]),
    ).toBe("M 0,0 L 100,50");
  });

  it("renders segmented paths through waypoints", () => {
    expect(
      waypointPath([
        { x: 0, y: 0 },
        { x: 40, y: 10 },
        { x: 80, y: 10 },
        { x: 120, y: 0 },
      ]),
    ).toBe("M 0,0 L 40,10 L 80,10 L 120,0");
  });
});

describe("approachStub", () => {
  it("offsets along the handle normal for each position", () => {
    const p = { x: 100, y: 100 };
    expect(approachStub(p, Position.Top, 18)).toEqual({ x: 100, y: 82 });
    expect(approachStub(p, Position.Bottom, 18)).toEqual({ x: 100, y: 118 });
    expect(approachStub(p, Position.Left, 18)).toEqual({ x: 82, y: 100 });
    expect(approachStub(p, Position.Right, 18)).toEqual({ x: 118, y: 100 });
  });

  it("returns the anchor unchanged for an unknown position", () => {
    expect(approachStub({ x: 5, y: 6 }, undefined, 18)).toEqual({ x: 5, y: 6 });
  });
});

describe("routePoints", () => {
  it("makes the final segment run straight into the target arrow", () => {
    // A single waypoint creates the vertical drop; without a stub the last
    // segment (waypoint -> target) would be diagonal. The target stub forces a
    // straight vertical approach into a Top handle.
    const points = routePoints(
      { x: 0, y: 0 },
      Position.Bottom,
      [{ x: 0, y: 100 }],
      { x: 60, y: 120 },
      Position.Top,
      18,
    );
    const last = points[points.length - 1];
    const beforeLast = points[points.length - 2];
    expect(last).toEqual({ x: 60, y: 120 });
    expect(beforeLast).toEqual({ x: 60, y: 102 });
    // Straight (vertical) approach: same x as the target handle.
    expect(beforeLast.x).toBe(last.x);
  });

  it("makes the first segment leave the source straight", () => {
    const points = routePoints(
      { x: 0, y: 0 },
      Position.Bottom,
      [],
      { x: 60, y: 120 },
      Position.Top,
      18,
    );
    expect(points[0]).toEqual({ x: 0, y: 0 });
    expect(points[1]).toEqual({ x: 0, y: 18 });
    expect(points[1].x).toBe(points[0].x);
  });

  it("collapses zero-length stub segments for unknown handle positions", () => {
    const points = routePoints(
      { x: 0, y: 0 },
      undefined,
      [],
      { x: 100, y: 50 },
      undefined,
      18,
    );
    expect(points).toEqual([
      { x: 0, y: 0 },
      { x: 100, y: 50 },
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
