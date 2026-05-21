import { describe, expect, it } from "vitest";

import { waypointPath } from "./waypoint-edge";

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
