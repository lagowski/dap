"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
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
  type Node,
  type NodeChange,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
} from "@xyflow/react";
import { useAgentsList, useCreatePipeline, useUpdatePipeline, useValidatePipeline } from "@/hooks/api";
import {
  annotationTooltip,
  computeEdgeAnnotation,
  END_SENTINEL,
  formatEdgeLabel,
  START_SENTINEL,
  type EdgeAnnotation,
} from "@/lib/edge-annotations";
import type {
  Agent,
  EdgeCondition,
  Pipeline,
  PipelineEdge,
  PipelineNode,
  ValidationResult,
} from "@/lib/api/types";
import { AgentPalette } from "./agent-palette";
import { Inspector } from "./inspector";
import { DesignerToolbar } from "./toolbar";
import {
  DEFAULT_DEFAULTS,
  STATE_SCHEMA_REF,
  type DesignerEdge,
  type DesignerNode,
  type PipelineFormPayload,
} from "./types";

const EDGE_WARNING_STROKE = "#dc2626"; // red-600
const EDGE_CONDITION_STROKE = "#3b82f6"; // blue-500
const EDGE_DEFAULT_STROKE = "#94a3b8"; // slate-400

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

export function PipelineDesigner({
  initialPipeline,
  seedFromPipeline = null,
}: PipelineDesignerProps) {
  const router = useRouter();
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

  const [nodes, setNodes] = useState<Node[]>(() =>
    (seed?.nodes ?? []).map(toReactFlowNode),
  );
  const [edges, setEdges] = useState<Edge[]>(() =>
    (seed?.edges ?? []).map(toReactFlowEdge),
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

  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);

  const onNodesChange: OnNodesChange = useCallback(
    (changes: NodeChange[]) => setNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );
  const onEdgesChange: OnEdgesChange = useCallback(
    (changes: EdgeChange[]) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    [],
  );
  const onConnect: OnConnect = useCallback(
    (params: Connection) => {
      const edgeId = `e_${Math.random().toString(36).slice(2, 8)}`;
      setEdges((eds) =>
        addEdge({ ...params, id: edgeId }, eds),
      );
      setEdgeMeta((meta) => ({ ...meta, [edgeId]: { condition: null, label: null } }));
    },
    [],
  );

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

  // Lifted from buildPayload so the Inspector can reuse the projections.
  const designerNodes = useMemo<PipelineNode[]>(
    () =>
      nodes.map((n) => ({
        id: n.id,
        agent_id: String((n.data as { agentId?: string })?.agentId ?? ""),
        position: { x: n.position.x, y: n.position.y },
      })),
    [nodes],
  );
  const designerEdges = useMemo<PipelineEdge[]>(
    () =>
      edges.map((e) => {
        const meta = edgeMeta[e.id];
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          condition: meta?.condition ?? null,
          label: meta?.label ?? null,
        };
      }),
    [edges, edgeMeta],
  );

  const buildPayload = useCallback(
    (): PipelineFormPayload => ({
      name,
      description,
      schema_version: "langgraph/1.0",
      state_schema_ref: STATE_SCHEMA_REF,
      entry_point: entryPoint,
      nodes: designerNodes,
      edges: designerEdges,
      defaults: DEFAULT_DEFAULTS,
    }),
    [name, description, entryPoint, designerNodes, designerEdges],
  );

  const validate = useValidatePipeline();
  const create = useCreatePipeline();
  const update = useUpdatePipeline();

  const handleValidate = useCallback(async () => {
    // Clear any previous result first — otherwise a stale "valid" sticks
    // around if the next validation attempt fails (and the Save button
    // would stay enabled based on the old success state).
    setValidationResult(null);
    try {
      const result = await validate.mutateAsync(buildPayload());
      setValidationResult(result);
    } catch {
      // The mutation's error state already drives the UI via submitError;
      // swallow here to avoid an unhandled rejection in the dev overlay.
    }
  }, [buildPayload, validate]);

  const handleSave = useCallback(async () => {
    const payload = buildPayload();
    try {
      if (initialPipeline) {
        const updated = await update.mutateAsync({
          id: initialPipeline.id,
          payload,
        });
        router.push(`/pipelines/${updated.id}/edit`);
      } else {
        const created = await create.mutateAsync(payload);
        router.push(`/pipelines/${created.id}/edit`);
      }
    } catch {
      // Save failure surfaces via create.error / update.error on the
      // toolbar; swallow here to avoid the unhandled rejection.
    }
  }, [buildPayload, initialPipeline, create, update, router]);

  // Single source-of-truth map: edge_id → EdgeAnnotation. Both the
  // rendered edges and the inspector read from this so the chip on
  // the canvas and the field list in the side panel can never drift.
  const annotations = useMemo<Map<string, EdgeAnnotation>>(() => {
    const agentsById = new Map<string, Agent>(agents.map((a) => [a.id, a]));
    const agentForReactFlowNode = (nodeId: string): Agent | undefined => {
      // Sentinels never resolve to an agent — return undefined so the
      // empty-annotation branch below kicks in (no chip, no warning).
      if (nodeId === START_SENTINEL || nodeId === END_SENTINEL) {
        return undefined;
      }
      const node = nodes.find((n) => n.id === nodeId);
      if (node === undefined) return undefined;
      const agentId = String((node.data as { agentId?: string })?.agentId ?? "");
      return agentsById.get(agentId);
    };
    const out = new Map<string, EdgeAnnotation>();
    for (const e of edges) {
      // Sentinel-touching edges have no contract to evaluate.
      if (e.target === END_SENTINEL || e.source === START_SENTINEL) {
        out.set(e.id, { fields: [], warning: false, unknown: false });
        continue;
      }
      out.set(
        e.id,
        computeEdgeAnnotation(
          agentForReactFlowNode(e.source),
          agentForReactFlowNode(e.target),
        ),
      );
    }
    return out;
  }, [edges, nodes, agents]);

  // Decorate React Flow edges with field annotations (#62). Original
  // ``edges`` stays the source of truth; we only rewrite cosmetic
  // props (label / style / data) using the precomputed map above.
  const annotatedEdges = useMemo<Edge[]>(() => {
    return edges.map((e) => {
      const annotation =
        annotations.get(e.id) ?? { fields: [], warning: false, unknown: false };
      const meta = edgeMeta[e.id];
      const hasCondition = meta?.condition != null;
      const userLabel = meta?.label?.trim() ?? "";
      const annotationLabel = formatEdgeLabel(annotation, hasCondition);
      // User-set edge label takes priority — they wrote it deliberately.
      // When unset we fall back to the auto annotation chip.
      const label = userLabel.length > 0 ? userLabel : annotationLabel;
      const stroke = annotation.warning
        ? EDGE_WARNING_STROKE
        : hasCondition
          ? EDGE_CONDITION_STROKE
          : EDGE_DEFAULT_STROKE;
      return {
        ...e,
        label,
        labelStyle: annotation.warning
          ? { fill: EDGE_WARNING_STROKE, fontWeight: 600 }
          : undefined,
        labelBgStyle: annotation.warning ? { fill: "#fee2e2" } : undefined,
        animated: hasCondition,
        style: { stroke, strokeWidth: annotation.warning ? 2 : 1 },
        data: {
          ...(e.data as object | undefined),
          tooltip: annotationTooltip(annotation),
          annotation,
        },
      };
    });
  }, [edges, edgeMeta, annotations]);

  // Compute current selection details for inspector. Pulls the
  // edge's annotation from the shared ``annotations`` map so the
  // inspector view never disagrees with the chip on the canvas.
  const selectionDetail = useMemo<
    | { kind: "node"; node: DesignerNode }
    | { kind: "edge"; edge: DesignerEdge; annotation: EdgeAnnotation }
    | { kind: "none" }
  >(() => {
    if (selection.kind === "node") {
      const n = nodes.find((x) => x.id === selection.id);
      if (!n) return { kind: "none" };
      const node: DesignerNode = {
        id: n.id,
        agent_id: String((n.data as { agentId?: string })?.agentId ?? ""),
        position: { x: n.position.x, y: n.position.y },
      };
      return { kind: "node", node };
    }
    if (selection.kind === "edge") {
      const e = edges.find((x) => x.id === selection.id);
      if (!e) return { kind: "none" };
      const meta = edgeMeta[e.id];
      const edge: DesignerEdge = {
        id: e.id,
        source: e.source,
        target: e.target,
        condition: meta?.condition ?? null,
        label: meta?.label ?? null,
      };
      const annotation =
        annotations.get(e.id) ?? { fields: [], warning: false, unknown: false };
      return { kind: "edge", edge, annotation };
    }
    return { kind: "none" };
  }, [selection, nodes, edges, edgeMeta, annotations]);

  const isSaving = create.isPending || update.isPending;
  const saveLabel = initialPipeline ? `Save v${initialPipeline.version + 1}` : "Save";
  // Surface server-side errors (422 from validate / save) in the toolbar
  // so the user sees the actual reason instead of just a dev-overlay flash.
  const submitError = create.error ?? update.error ?? validate.error;

  return (
    <div className="flex flex-col h-full">
      <DesignerToolbar
        name={name}
        description={description}
        onNameChange={setName}
        onDescriptionChange={setDescription}
        onValidate={handleValidate}
        onSave={handleSave}
        validationResult={validationResult}
        isValidating={validate.isPending}
        isSaving={isSaving}
        saveLabel={saveLabel}
        submitError={submitError}
        pipelineId={initialPipeline?.id}
        pipelineVersion={initialPipeline?.version}
        pipeline={initialPipeline}
      />
      <div className="flex flex-1 overflow-hidden">
        <AgentPalette onAddNode={handleAddNode} />
        <div className="flex-1 bg-muted/30">
          <ReactFlow
            nodes={nodes}
            edges={annotatedEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_e, node) => setSelection({ kind: "node", id: node.id })}
            onEdgeClick={(_e, edge) => setSelection({ kind: "edge", id: edge.id })}
            onPaneClick={() => setSelection({ kind: "none" })}
            fitView
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
        />
      </div>
    </div>
  );
}

function toReactFlowNode(n: PipelineNode): Node {
  return {
    id: n.id,
    type: "default",
    position: n.position,
    data: { label: n.id, agentId: n.agent_id },
  };
}

function toReactFlowEdge(e: PipelineEdge): Edge {
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.condition ? "if" : undefined,
    animated: e.condition != null,
  };
}
