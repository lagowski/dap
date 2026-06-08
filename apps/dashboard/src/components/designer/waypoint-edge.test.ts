import { describe, expect, it } from "vitest";

import { nudgeWaypoint, waypointPath } from "./waypoint-edge";

describe("waypointPath", () => {
  it("renders an empty path without points", () => {
    expect(waypointPath([])).toBe("");
  });

  it("routes a diagonal segment orthogonally (H→V→H through mid-x)", () => {
    expect(
      waypointPath([
        { x: 0, y: 0 },
        { x: 100, y: 50 },
      ]),
    ).toBe("M 0,0 L 50,0 L 50,50 L 100,50");
  });

  it("keeps horizontal/vertical segments straight, only bends diagonals", () => {
    expect(
      waypointPath([
        { x: 0, y: 0 },
        { x: 40, y: 10 },
        { x: 80, y: 10 },
        { x: 120, y: 0 },
      ]),
    ).toBe("M 0,0 L 20,0 L 20,10 L 40,10 L 80,10 L 100,10 L 100,0 L 120,0");
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
