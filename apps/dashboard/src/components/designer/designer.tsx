"use client";

/**
 * Pipeline designer orchestrator (audit D1 slice 5).
 *
 * Post-split this file owns the designer state (React Flow nodes /
 * edges + designer-domain edge metadata + selection) and the
 * mutation handlers that drive it. Derived projections + server
 * save / validate live in dedicated hooks:
 *
 * - ``use-pipeline-projections``    — derived memos (canonical-
 *                                      shape lists, annotations,
 *                                      annotated edges, selection
 *                                      detail)
 * - ``use-pipeline-save``           — buildPayload + validate /
 *                                      create / update mutations +
 *                                      handlers
 * - ``reactflow-adapters``          — pure model → React Flow
 *                                      shape conversion + stroke
 *                                      colours
 *
 * Pre-split this file was 512 LOC; post-split it stays under 260.
 */

import { useCallback, useMemo, useState } from "react";

import {
  Background,
  Controls,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type EdgeTypes,
  type Node,
  type NodeChange,
  type OnConnect,
  type OnEdgesChange,
  type OnMoveEnd,
  type OnNodesChange,
  type Viewport,
  type XYPosition,
} from "@xyflow/react";

import { useAgentsList } from "@/hooks/api";
import type {
  Agent,
  EdgeCondition,
  Pipeline,
} from "@/lib/api/types";

import { AgentPalette } from "./agent-palette";
import { Inspector } from "./inspector";
import {
  parseEdgeWaypoints,
  toReactFlowEdge,
  toReactFlowNode,
  type EdgeWaypoints,
} from "./reactflow-adapters";
import { DesignerToolbar } from "./toolbar";
import { usePipelineProjections } from "./use-pipeline-projections";
import { useAutoSaveLayout, usePipelineSave } from "./use-pipeline-save";
import { WaypointEdge } from "./waypoint-edge";

interface PipelineDesignerProps {
  /** The persisted pipeline being edited; ``null`` for a fresh pipeline. */
  initialPipeline: Pipeline | null;
  /**
   * Source pipeline to copy nodes/edges/defaults from when creating a
   * fresh pipeline (Clone flow, #124). Different from
   * ``initialPipeline`` because the save path stays in *create* mode —
   * we don't want Clone to bump a v2 of the source. ``null`` for a
   * scratch pipeline, ignored when ``initialPipeline`` is non-null.
   */
  seedFromPipeline?: Pipeline | null;
}

const NEW_NODE_OFFSET = 80;
const EDGE_TYPES: EdgeTypes = { waypoint: WaypointEdge };

type DesignerEdgeData = {
  waypoints?: unknown;
  onWaypointsChange?: (edgeId: string, waypoints: XYPosition[]) => void;
};

function designerEdgeData(data: unknown): DesignerEdgeData {
  if (data == null || typeof data !== "object") return {};

  const waypoints = Reflect.get(data, "waypoints");
  const onWaypointsChange = Reflect.get(data, "onWaypointsChange");

  return {
    ...(waypoints !== undefined ? { waypoints } : {}),
    ...(typeof onWaypointsChange === "function"
      ? {
          onWaypointsChange: (edgeId: string, nextWaypoints: XYPosition[]) => {
            onWaypointsChange(edgeId, nextWaypoints);
          },
        }
      : {}),
  };
}

function parseSavedViewport(uiMetadata: Record<string, unknown> | undefined): Viewport | null {
  const viewport = uiMetadata?.viewport;
  if (!viewport || typeof viewport !== "object") return null;
  const candidate = viewport as Partial<Viewport>;
  if (
    typeof candidate.x !== "number" ||
    typeof candidate.y !== "number" ||
    typeof candidate.zoom !== "number" ||
    !Number.isFinite(candidate.x) ||
    !Number.isFinite(candidate.y) ||
    !Number.isFinite(candidate.zoom) ||
    candidate.zoom < 0.1 ||
    candidate.zoom > 4
  ) {
    return null;
  }
  return { x: candidate.x, y: candidate.y, zoom: candidate.zoom };
}


export function PipelineDesigner({
  initialPipeline,
  seedFromPipeline = null,
}: PipelineDesignerProps) {
  const { data: agentsData } = useAgentsList();
  // Stable reference so memos that depend on `agents` don't re-run when
  // useAgentsList re-renders without a real data change.
  const agents = useMemo<Agent[]>(
    () => agentsData?.items ?? [],
    [agentsData?.items],
  );

  // Edit-mode source: ``initialPipeline`` (existing pipeline being
  // edited). Clone-mode source: ``seedFromPipeline`` (existing pipeline
  // duplicated into a fresh one). Both paint the form the same way;
  // only ``initialPipeline`` flips the save path into update mode.
  const seed = initialPipeline ?? seedFromPipeline;
  // Default the cloned name with a "(copy)" suffix so the user can
  // save without renaming and still tell which is which in lists.
  // Edit mode keeps the original name verbatim.
  const initialName =
    initialPipeline?.name ??
    (seedFromPipeline ? `${seedFromPipeline.name} (copy)` : "");

  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(seed?.description ?? "");
  const [entryPoint, setEntryPoint] = useState(seed?.entry_point ?? "");
  const savedViewport = parseSavedViewport(seed?.ui_metadata);
  const savedEdgeWaypoints = parseEdgeWaypoints(seed?.ui_metadata);
  const [viewport, setViewport] = useState<Viewport | null>(savedViewport);

  const [nodes, setNodes] = useState<Node[]>(() => {
    // Prefer positions stored in ui_metadata.node_positions (saved by
    // buildPayload on every Save). Falls back to PipelineNode.position for
    // older pipelines / first-load before any Save (#226).
    const savedPositions = seed?.ui_metadata?.node_positions as
      | Record<string, { x: number; y: number }>
      | undefined;
    return (seed?.nodes ?? []).map((n) => {
      const pos = savedPositions?.[n.id];
      return toReactFlowNode(pos ? { ...n, position: pos } : n);
    });
  });
  const [edges, setEdges] = useState<Edge[]>(() =>
    (seed?.edges ?? []).map((edge) =>
      toReactFlowEdge(edge, savedEdgeWaypoints[edge.id] ?? []),
    ),
  );

  // Designer-domain edge metadata that React Flow doesn't know about.
  const [edgeMeta, setEdgeMeta] = useState<
    Record<string, { condition: EdgeCondition | null; label: string | null }>
  >(() => {
    const initial: Record<string, { condition: EdgeCondition | null; label: string | null }> = {};
    for (const e of seed?.edges ?? []) {
      initial[e.id] = { condition: e.condition ?? null, label: e.label ?? null };
    }
    return initial;
  });

  const [selection, setSelection] = useState<
    | { kind: "node"; id: string }
    | { kind: "edge"; id: string }
    | { kind: "none" }
  >({ kind: "none" });

  // Per-node LLM/backend assignments (#755 follow-up). Seeded from the pipeline
  // so an edit doesn't drop them; the inspector's "LLM per node" panel edits it.
  const [backendProfiles, setBackendProfiles] = useState<Record<string, unknown> | null>(
    () => (seed?.backend_profiles as Record<string, unknown> | undefined) ?? null,
  );

  const onNodesChange: OnNodesChange = useCallback(
    (changes: NodeChange[]) => setNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );
  const onEdgesChange: OnEdgesChange = useCallback(
    (changes: EdgeChange[]) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    [],
  );
  const onMoveEnd: OnMoveEnd = useCallback((_event, nextViewport) => {
    setViewport(nextViewport);
  }, []);
  const onConnect: OnConnect = useCallback((params: Connection) => {
    const edgeId = `e_${Math.random().toString(36).slice(2, 8)}`;
    setEdges((eds) =>
      addEdge({ ...params, id: edgeId, type: "waypoint", data: { waypoints: [] } }, eds),
    );
    setEdgeMeta((meta) => ({ ...meta, [edgeId]: { condition: null, label: null } }));
  }, []);

  const handleAddNode = useCallback(
    (agentId: string, agentName: string) => {
      const nodeId = `n_${Math.random().toString(36).slice(2, 8)}`;
      const position = {
        x: nodes.length * NEW_NODE_OFFSET,
        y: nodes.length * NEW_NODE_OFFSET,
      };
      const newNode: Node = {
        id: nodeId,
        type: "default",
        position,
        data: { label: agentName, agentId },
      };
      setNodes((nds) => [...nds, newNode]);
      if (entryPoint === "") setEntryPoint(nodeId);
    },
    [nodes.length, entryPoint],
  );

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== nodeId));
      setEdges((eds) => {
        const removed = eds.filter((e) => e.source === nodeId || e.target === nodeId);
        if (removed.length > 0) {
          setEdgeMeta((meta) => {
            const next = { ...meta };
            for (const e of removed) delete next[e.id];
            return next;
          });
        }
        return eds.filter((e) => e.source !== nodeId && e.target !== nodeId);
      });
      if (selection.kind === "node" && selection.id === nodeId) {
        setSelection({ kind: "none" });
      }
    },
    [selection],
  );

  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      setEdges((eds) => eds.filter((e) => e.id !== edgeId));
      setEdgeMeta((meta) => {
        const next = { ...meta };
        delete next[edgeId];
        return next;
      });
      if (selection.kind === "edge" && selection.id === edgeId) {
        setSelection({ kind: "none" });
      }
    },
    [selection],
  );

  const handleUpdateEdgeCondition = useCallback(
    (edgeId: string, condition: EdgeCondition | null) => {
      setEdgeMeta((meta) => ({
        ...meta,
        [edgeId]: { ...(meta[edgeId] ?? { label: null, condition: null }), condition },
      }));
      // Also update the React Flow edge label to indicate "if"
      setEdges((eds) =>
        eds.map((e) =>
          e.id === edgeId
            ? { ...e, label: condition ? "if" : undefined, animated: condition != null }
            : e,
        ),
      );
    },
    [],
  );

  const handleUpdateEdgeLabel = useCallback(
    (edgeId: string, label: string) => {
      const trimmed = label.trim();
      const stored = trimmed.length > 0 ? trimmed : null;
      setEdgeMeta((meta) => ({
        ...meta,
        [edgeId]: { ...(meta[edgeId] ?? { condition: null, label: null }), label: stored },
      }));
    },
    [],
  );

  const handleUpdateEdgeWaypoints = useCallback(
    (edgeId: string, waypoints: XYPosition[]) => {
      setEdges((eds) =>
        eds.map((edge) =>
          edge.id === edgeId
            ? {
                ...edge,
                type: "waypoint",
                data: { ...designerEdgeData(edge.data), waypoints },
              }
            : edge,
        ),
      );
    },
    [],
  );

  const { designerNodes, designerEdges, annotatedEdges, selectionDetail } =
    usePipelineProjections({ nodes, edges, edgeMeta, agents, selection });
  const renderedEdges = useMemo<Edge[]>(
    () =>
      annotatedEdges.map((edge) => ({
        ...edge,
        type: "waypoint",
        data: {
          ...designerEdgeData(edge.data),
          onWaypointsChange: handleUpdateEdgeWaypoints,
        },
      })),
    [annotatedEdges, handleUpdateEdgeWaypoints],
  );
  const edgeWaypoints = useMemo<EdgeWaypoints>(() => {
    const out: EdgeWaypoints = {};
    for (const edge of edges) {
      const waypoints = designerEdgeData(edge.data).waypoints;
      if (!Array.isArray(waypoints)) continue;
      const valid = waypoints.filter(
        (point): point is XYPosition =>
          point != null &&
          typeof point === "object" &&
          typeof (point as Partial<XYPosition>).x === "number" &&
          typeof (point as Partial<XYPosition>).y === "number" &&
          Number.isFinite((point as Partial<XYPosition>).x) &&
          Number.isFinite((point as Partial<XYPosition>).y),
      );
      if (valid.length > 0) {
        out[edge.id] = valid.map((point) => ({ x: point.x, y: point.y }));
      }
    }
    return out;
  }, [edges]);

  const save = usePipelineSave({
    name,
    description,
    entryPoint,
    designerNodes,
    designerEdges,
    initialPipeline,
    viewport,
    edgeWaypoints,
    backendProfiles,
  });
  const layoutSave = useAutoSaveLayout({
    pipelineId: initialPipeline?.id ?? null,
    designerNodes,
    existingUiMetadata: initialPipeline?.ui_metadata ?? null,
    viewport,
    edgeWaypoints,
  });

  return (
    <div className="flex flex-col h-full">
      <DesignerToolbar
        name={name}
        description={description}
        onNameChange={setName}
        onDescriptionChange={setDescription}
        onValidate={save.handleValidate}
        onSave={save.handleSave}
        validationResult={save.validationResult}
        isValidating={save.isValidating}
        isSaving={save.isSaving}
        saveLabel={save.saveLabel}
        submitError={save.submitError}
        layoutSaveStatus={layoutSave.status}
        layoutSavedAt={layoutSave.savedAt}
        layoutSaveError={layoutSave.error}
        pipelineId={initialPipeline?.id}
        pipelineVersion={initialPipeline?.version}
        pipeline={initialPipeline}
      />
      <div className="flex flex-1 overflow-hidden">
        <AgentPalette onAddNode={handleAddNode} />
        <div className="flex-1 bg-muted/30">
          <ReactFlow
            nodes={nodes}
            edges={renderedEdges}
            edgeTypes={EDGE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_e, node) => setSelection({ kind: "node", id: node.id })}
            onEdgeClick={(_e, edge) => setSelection({ kind: "edge", id: edge.id })}
            onPaneClick={() => setSelection({ kind: "none" })}
            onMoveEnd={onMoveEnd}
            defaultViewport={savedViewport ?? undefined}
            fitView={savedViewport == null}
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
        <Inspector
          selection={selectionDetail}
          agents={agents}
          allNodes={designerNodes}
          allEdges={designerEdges}
          entryPoint={entryPoint}
          onSetEntryPoint={setEntryPoint}
          onDeleteNode={handleDeleteNode}
          onDeleteEdge={handleDeleteEdge}
          onUpdateEdgeCondition={handleUpdateEdgeCondition}
          onUpdateEdgeLabel={handleUpdateEdgeLabel}
          backendProfiles={backendProfiles}
          onBackendProfilesChange={setBackendProfiles}
        />
      </div>
    </div>
  );
}
