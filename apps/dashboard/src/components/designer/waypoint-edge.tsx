"use client";

import {
  BaseEdge,
  EdgeLabelRenderer,
  type EdgeProps,
  Position,
  type XYPosition,
  useReactFlow,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef } from "react";
import type React from "react";

/** Distance (flow units) of the straight approach/exit stub off each handle. */
export const APPROACH_STUB = 18;

type WaypointEdgeData = {
  waypoints?: XYPosition[];
  onWaypointsChange?: (edgeId: string, waypoints: XYPosition[]) => void;
  tooltip?: string;
};

const EMPTY_WAYPOINTS: XYPosition[] = [];

export function waypointPath(points: XYPosition[]): string {
  if (points.length === 0) return "";
  // Orthogonal (right-angle / "broken-line") routing (#765): each segment that
  // isn't already horizontal or vertical is drawn as H→V→H through the segment's
  // mid-x, so edges run in clean right angles instead of diagonals.
  let path = `M ${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (a.x === b.x || a.y === b.y) {
      path += ` L ${b.x},${b.y}`;
    } else {
      const midX = (a.x + b.x) / 2;
      path += ` L ${midX},${a.y} L ${midX},${b.y} L ${b.x},${b.y}`;
    }
  }
  return path;
}

/** Offset a handle anchor outward along its normal, so the segment touching the
 *  node runs straight into (or out of) the handle instead of approaching at an
 *  angle. Top/Bottom shift vertically, Left/Right horizontally. */
export function approachStub(
  point: XYPosition,
  position: Position | undefined,
  distance = APPROACH_STUB,
): XYPosition {
  switch (position) {
    case Position.Left:
      return { x: point.x - distance, y: point.y };
    case Position.Right:
      return { x: point.x + distance, y: point.y };
    case Position.Top:
      return { x: point.x, y: point.y - distance };
    case Position.Bottom:
      return { x: point.x, y: point.y + distance };
    default:
      return { ...point };
  }
}

/** Build the full point list for an edge: a straight exit stub off the source
 *  handle, the manual waypoints, then a straight approach stub into the target
 *  handle. The last segment (stub → target) is always colinear with the arrow. */
export function routePoints(
  source: XYPosition,
  sourcePosition: Position | undefined,
  waypoints: XYPosition[],
  target: XYPosition,
  targetPosition: Position | undefined,
  distance = APPROACH_STUB,
): XYPosition[] {
  const candidates = [
    source,
    approachStub(source, sourcePosition, distance),
    ...waypoints,
    approachStub(target, targetPosition, distance),
    target,
  ];
  // Drop consecutive duplicates (e.g. an unknown handle position whose stub
  // coincides with its anchor) so we never emit a zero-length segment.
  return candidates.filter(
    (point, index) =>
      index === 0 ||
      point.x !== candidates[index - 1].x ||
      point.y !== candidates[index - 1].y,
  );
}

export function nudgeWaypoint(
  point: XYPosition,
  key: string,
  step = 12,
): XYPosition {
  switch (key) {
    case "ArrowUp":
      return { ...point, y: point.y - step };
    case "ArrowDown":
      return { ...point, y: point.y + step };
    case "ArrowLeft":
      return { ...point, x: point.x - step };
    case "ArrowRight":
      return { ...point, x: point.x + step };
    default:
      return point;
  }
}

function midpoint(points: XYPosition[]): XYPosition {
  const index = Math.floor((points.length - 1) / 2);
  const start = points[index];
  const end = points[index + 1] ?? start;
  return { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
}

export function WaypointEdge({
  id,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  markerEnd,
  style,
  label,
  labelStyle,
  labelBgStyle,
  data,
}: EdgeProps) {
  const { screenToFlowPosition } = useReactFlow();
  const dragCleanupRef = useRef<(() => void) | null>(null);
  const edgeData = (data ?? {}) as WaypointEdgeData;
  const waypoints = edgeData.waypoints ?? EMPTY_WAYPOINTS;
  const latestWaypointsRef = useRef(waypoints);
  const latestOnWaypointsChangeRef = useRef(edgeData.onWaypointsChange);
  const pathPoints = useMemo(
    () =>
      routePoints(
        { x: sourceX, y: sourceY },
        sourcePosition,
        waypoints,
        { x: targetX, y: targetY },
        targetPosition,
      ),
    [sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, waypoints],
  );
  const path = waypointPath(pathPoints);
  const labelPosition = midpoint(pathPoints);

  useEffect(() => {
    latestWaypointsRef.current = waypoints;
    latestOnWaypointsChangeRef.current = edgeData.onWaypointsChange;
  }, [waypoints, edgeData.onWaypointsChange]);

  const cleanupDrag = useCallback(() => {
      dragCleanupRef.current?.();
      dragCleanupRef.current = null;
  }, []);

  useEffect(() => cleanupDrag, [cleanupDrag]);

  const updateWaypoints = useCallback(
    (next: XYPosition[]) => {
      latestOnWaypointsChangeRef.current?.(id, next);
    },
    [id],
  );

  const addWaypoint = (event: React.MouseEvent<SVGPathElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const point = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    updateWaypoints([...latestWaypointsRef.current, point]);
  };

  const addWaypointFromKeyboard = (event: React.KeyboardEvent<SVGPathElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.stopPropagation();
    updateWaypoints([...latestWaypointsRef.current, midpoint(pathPoints)]);
  };

  const removeWaypoint = (event: React.MouseEvent<SVGCircleElement>, index: number) => {
    event.preventDefault();
    event.stopPropagation();
    updateWaypoints(
      latestWaypointsRef.current.filter((_, itemIndex) => itemIndex !== index),
    );
  };

  const editWaypointFromKeyboard = (
    event: React.KeyboardEvent<SVGCircleElement>,
    index: number,
  ) => {
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      event.stopPropagation();
      updateWaypoints(
        latestWaypointsRef.current.filter((_, itemIndex) => itemIndex !== index),
      );
      return;
    }
    if (!event.key.startsWith("Arrow")) return;
    event.preventDefault();
    event.stopPropagation();
    updateWaypoints(
      latestWaypointsRef.current.map((point, itemIndex) =>
        itemIndex === index ? nudgeWaypoint(point, event.key, event.shiftKey ? 3 : 12) : point,
      ),
    );
  };

  const startDrag = (event: React.PointerEvent<SVGCircleElement>, index: number) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    cleanupDrag();
    const move = (moveEvent: PointerEvent) => {
      const point = screenToFlowPosition({ x: moveEvent.clientX, y: moveEvent.clientY });
      updateWaypoints(
        latestWaypointsRef.current.map((item, itemIndex) =>
          itemIndex === index ? point : item,
        ),
      );
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      dragCleanupRef.current = null;
    };
    dragCleanupRef.current = stop;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  };

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      <path
        d={path}
        fill="none"
        stroke="transparent"
        strokeWidth={18}
        className="react-flow__edge-interaction focus:outline-none focus-visible:stroke-primary"
        onDoubleClick={addWaypoint}
        onKeyDown={addWaypointFromKeyboard}
        tabIndex={0}
        role="button"
        aria-label="Add edge waypoint"
      >
        {edgeData.tooltip ? <title>{edgeData.tooltip}</title> : null}
      </path>
      {waypoints.map((point, index) => (
        <circle
          key={`${id}-wp-${index}`}
          cx={point.x}
          cy={point.y}
          r={6}
          className="fill-background stroke-primary"
          strokeWidth={2}
          onPointerDown={(event) => startDrag(event, index)}
          onDoubleClick={(event) => removeWaypoint(event, index)}
          onKeyDown={(event) => editWaypointFromKeyboard(event, index)}
          tabIndex={0}
          role="button"
          aria-label={`Delete waypoint ${index + 1} of ${waypoints.length}; use arrow keys to move`}
        />
      ))}
      {label ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan rounded border bg-background px-1.5 py-0.5 text-[10px] shadow-sm"
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelPosition.x}px,${labelPosition.y}px)`,
              pointerEvents: "all",
              ...(labelBgStyle && "fill" in labelBgStyle
                ? { backgroundColor: String(labelBgStyle.fill) }
                : {}),
              ...(labelStyle && "fill" in labelStyle
                ? { color: String(labelStyle.fill) }
                : {}),
              ...(labelStyle && "fontWeight" in labelStyle
                ? { fontWeight: Number(labelStyle.fontWeight) }
                : {}),
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}
