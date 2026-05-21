"use client";

import {
  BaseEdge,
  EdgeLabelRenderer,
  type EdgeProps,
  type XYPosition,
  useReactFlow,
} from "@xyflow/react";
import { useEffect, useMemo, useRef } from "react";
import type React from "react";

type WaypointEdgeData = {
  waypoints?: XYPosition[];
  onWaypointsChange?: (edgeId: string, waypoints: XYPosition[]) => void;
  tooltip?: string;
};

const EMPTY_WAYPOINTS: XYPosition[] = [];

export function waypointPath(points: XYPosition[]): string {
  if (points.length === 0) return "";
  const [first, ...rest] = points;
  return rest.reduce((path, point) => `${path} L ${point.x},${point.y}`, `M ${first.x},${first.y}`);
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
  targetX,
  targetY,
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
  const pathPoints = useMemo(
    () => [
      { x: sourceX, y: sourceY },
      ...waypoints,
      { x: targetX, y: targetY },
    ],
    [sourceX, sourceY, targetX, targetY, waypoints],
  );
  const path = waypointPath(pathPoints);
  const labelPosition = midpoint(pathPoints);

  useEffect(
    () => () => {
      dragCleanupRef.current?.();
      dragCleanupRef.current = null;
    },
    [],
  );

  const updateWaypoints = (next: XYPosition[]) => {
    edgeData.onWaypointsChange?.(id, next);
  };

  const addWaypoint = (event: React.MouseEvent<SVGPathElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const point = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    updateWaypoints([...waypoints, point]);
  };

  const addWaypointFromKeyboard = (event: React.KeyboardEvent<SVGPathElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.stopPropagation();
    updateWaypoints([...waypoints, midpoint(pathPoints)]);
  };

  const removeWaypoint = (event: React.MouseEvent<SVGCircleElement>, index: number) => {
    event.preventDefault();
    event.stopPropagation();
    updateWaypoints(waypoints.filter((_, itemIndex) => itemIndex !== index));
  };

  const editWaypointFromKeyboard = (
    event: React.KeyboardEvent<SVGCircleElement>,
    index: number,
  ) => {
    if (event.key === "Enter" || event.key === " " || event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      event.stopPropagation();
      updateWaypoints(waypoints.filter((_, itemIndex) => itemIndex !== index));
      return;
    }
    if (!event.key.startsWith("Arrow")) return;
    event.preventDefault();
    event.stopPropagation();
    updateWaypoints(
      waypoints.map((point, itemIndex) =>
        itemIndex === index ? nudgeWaypoint(point, event.key, event.shiftKey ? 3 : 12) : point,
      ),
    );
  };

  const startDrag = (event: React.PointerEvent<SVGCircleElement>, index: number) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragCleanupRef.current?.();
    const move = (moveEvent: PointerEvent) => {
      const point = screenToFlowPosition({ x: moveEvent.clientX, y: moveEvent.clientY });
      updateWaypoints(
        waypoints.map((item, itemIndex) => (itemIndex === index ? point : item)),
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
          aria-label={`Edit waypoint ${index + 1} of ${waypoints.length}`}
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
