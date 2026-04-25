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
import type {
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

interface PipelineDesignerProps {
  initialPipeline: Pipeline | null;
}

const NEW_NODE_OFFSET = 80;

export function PipelineDesigner({ initialPipeline }: PipelineDesignerProps) {
  const router = useRouter();
  const { data: agentsData } = useAgentsList();
  const agents = agentsData?.items ?? [];

  const [name, setName] = useState(initialPipeline?.name ?? "");
  const [description, setDescription] = useState(initialPipeline?.description ?? "");
  const [entryPoint, setEntryPoint] = useState(initialPipeline?.entry_point ?? "");

  const [nodes, setNodes] = useState<Node[]>(() =>
    (initialPipeline?.nodes ?? []).map(toReactFlowNode),
  );
  const [edges, setEdges] = useState<Edge[]>(() =>
    (initialPipeline?.edges ?? []).map(toReactFlowEdge),
  );

  // Designer-domain edge metadata that React Flow doesn't know about.
  const [edgeMeta, setEdgeMeta] = useState<
    Record<string, { condition: EdgeCondition | null; label: string | null }>
  >(() => {
    const initial: Record<string, { condition: EdgeCondition | null; label: string | null }> = {};
    for (const e of initialPipeline?.edges ?? []) {
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

  const buildPayload = useCallback((): PipelineFormPayload => {
    const designerNodes: PipelineNode[] = nodes.map((n) => ({
      id: n.id,
      agent_id: String((n.data as { agentId?: string })?.agentId ?? ""),
      position: { x: n.position.x, y: n.position.y },
    }));
    const designerEdges: PipelineEdge[] = edges.map((e) => {
      const meta = edgeMeta[e.id];
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        condition: meta?.condition ?? null,
        label: meta?.label ?? null,
      };
    });
    return {
      name,
      description,
      schema_version: "langgraph/1.0",
      state_schema_ref: STATE_SCHEMA_REF,
      entry_point: entryPoint,
      nodes: designerNodes,
      edges: designerEdges,
      defaults: DEFAULT_DEFAULTS,
    };
  }, [name, description, entryPoint, nodes, edges, edgeMeta]);

  const validate = useValidatePipeline();
  const create = useCreatePipeline();
  const update = useUpdatePipeline();

  const handleValidate = useCallback(async () => {
    const result = await validate.mutateAsync(buildPayload());
    setValidationResult(result);
  }, [buildPayload, validate]);

  const handleSave = useCallback(async () => {
    const payload = buildPayload();
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
  }, [buildPayload, initialPipeline, create, update, router]);

  // Compute current selection details for inspector
  const selectionDetail = useMemo<
    | { kind: "node"; node: DesignerNode }
    | { kind: "edge"; edge: DesignerEdge }
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
      return { kind: "edge", edge };
    }
    return { kind: "none" };
  }, [selection, nodes, edges, edgeMeta]);

  const isSaving = create.isPending || update.isPending;
  const saveLabel = initialPipeline ? `Save v${initialPipeline.version + 1}` : "Save";

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
      />
      <div className="flex flex-1 overflow-hidden">
        <AgentPalette onAddNode={handleAddNode} />
        <div className="flex-1 bg-muted/30">
          <ReactFlow
            nodes={nodes}
            edges={edges}
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
