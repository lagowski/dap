"use client";

import Link from "next/link";
import { ExternalLink, FlaskConical, Pencil, Trash2 } from "lucide-react";
import type { Agent, EdgeCondition, PipelineEdge, PipelineNode } from "@/lib/api/types";
import type { EdgeAnnotation } from "@/lib/edge-annotations";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { ConditionBuilder } from "./condition-builder";
import type { DesignerEdge, DesignerNode } from "./types";

type Selection =
  | { kind: "node"; node: DesignerNode }
  | { kind: "edge"; edge: DesignerEdge; annotation: EdgeAnnotation }
  | { kind: "none" };

interface InspectorProps {
  selection: Selection;
  agents: Agent[];
  // Full DAG snapshot — Node panel walks back through edges to compute cumulative upstream output_schema.
  allNodes: DesignerNode[];
  allEdges: DesignerEdge[];
  entryPoint: string;
  onSetEntryPoint: (nodeId: string) => void;
  onDeleteNode: (nodeId: string) => void;
  onDeleteEdge: (edgeId: string) => void;
  onUpdateEdgeCondition: (edgeId: string, condition: EdgeCondition | null) => void;
  onUpdateEdgeLabel: (edgeId: string, label: string) => void;
}

export function Inspector(props: InspectorProps) {
  return (
    <aside className="w-80 shrink-0 border-l bg-background overflow-y-auto">
      <div className="p-3 border-b">
        <h3 className="text-xs font-semibold uppercase text-muted-foreground">
          Inspector
        </h3>
      </div>
      <div className="p-3">
        {props.selection.kind === "none" && (
          <p className="text-xs text-muted-foreground">
            Click a node or edge to inspect.
          </p>
        )}
        {props.selection.kind === "node" && (
          <NodePanel
            node={props.selection.node}
            agents={props.agents}
            allNodes={props.allNodes}
            allEdges={props.allEdges}
            entryPoint={props.entryPoint}
            onSetEntryPoint={props.onSetEntryPoint}
            onDelete={props.onDeleteNode}
          />
        )}
        {props.selection.kind === "edge" && (
          <EdgePanel
            edge={props.selection.edge}
            annotation={props.selection.annotation}
            onUpdateCondition={(c) =>
              props.onUpdateEdgeCondition(props.selection.kind === "edge" ? props.selection.edge.id : "", c)
            }
            onUpdateLabel={(l) =>
              props.onUpdateEdgeLabel(props.selection.kind === "edge" ? props.selection.edge.id : "", l)
            }
            onDelete={props.onDeleteEdge}
          />
        )}
      </div>
    </aside>
  );
}

interface NodePanelProps {
  node: DesignerNode;
  agents: Agent[];
  allNodes: PipelineNode[];
  allEdges: PipelineEdge[];
  entryPoint: string;
  onSetEntryPoint: (nodeId: string) => void;
  onDelete: (nodeId: string) => void;
}

const PROMPT_PREVIEW_LIMIT = 240;

function NodePanel({
  node,
  agents,
  allNodes,
  allEdges,
  entryPoint,
  onSetEntryPoint,
  onDelete,
}: NodePanelProps) {
  const agent = agents.find((a) => a.id === node.agent_id);
  const isEntry = entryPoint === node.id;
  const stateFields = computeStateAfterNode(node.id, allNodes, allEdges, agents);

  return (
    <div className="space-y-3">
      <Field label="Node ID">
        <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
          {node.id}
        </code>
      </Field>

      <Field label="Agent">
        {agent ? (
          <div className="text-sm space-y-2">
            <div className="font-medium">{agent.name}</div>
            <div className="flex items-center gap-1 flex-wrap">
              <Badge variant="secondary">{agent.role}</Badge>
              <Badge variant="outline" className="font-mono text-[10px]">
                {agent.runtime_id}
              </Badge>
              <Badge variant="outline" className="text-[10px]">
                v{agent.version}
              </Badge>
            </div>
            <div className="flex gap-1.5 pt-1">
              <Button asChild variant="outline" size="sm" className="flex-1 h-7 text-xs">
                <Link href={`/agents/${agent.id}/edit`} target="_blank" rel="noopener noreferrer">
                  <Pencil className="h-3 w-3 mr-1" aria-hidden="true" />
                  Edit
                  <ExternalLink className="h-2.5 w-2.5 ml-1 opacity-60" aria-hidden="true" />
                </Link>
              </Button>
              <Button asChild variant="outline" size="sm" className="flex-1 h-7 text-xs">
                <Link
                  href={`/agents/${agent.id}/edit?tab=test`}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open the agent's Test panel in a new tab"
                >
                  <FlaskConical className="h-3 w-3 mr-1" aria-hidden="true" />
                  Test
                </Link>
              </Button>
            </div>
          </div>
        ) : (
          <span className="text-xs text-destructive">
            Agent not found: {node.agent_id}
          </span>
        )}
      </Field>

      {agent ? <AgentDetailsCollapse agent={agent} /> : null}

      <Field label="State after this node">
        <StateAfterNodeView fields={stateFields} />
      </Field>

      <Field label="Position">
        <code className="text-xs text-muted-foreground">
          x={Math.round(node.position.x)}, y={Math.round(node.position.y)}
        </code>
      </Field>

      <div className="pt-2 border-t space-y-2">
        {isEntry ? (
          <Badge variant="info">Entry point</Badge>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onSetEntryPoint(node.id)}
            className="w-full"
          >
            Set as entry point
          </Button>
        )}
        <Button
          type="button"
          variant="destructive"
          size="sm"
          onClick={() => onDelete(node.id)}
          className="w-full"
          disabled={isEntry}
          title={isEntry ? "Cannot delete entry node" : ""}
        >
          <Trash2 className="h-3 w-3 mr-1" />
          Delete node
        </Button>
      </div>
    </div>
  );
}

function AgentDetailsCollapse({ agent }: { agent: Agent }) {
  const promptShort = agent.prompt_template.length > PROMPT_PREVIEW_LIMIT;
  return (
    <details className="border rounded-md bg-muted/20">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Agent details
      </summary>
      <div className="px-3 pb-3 pt-1 space-y-3 text-xs">
        <div className="space-y-1">
          <Label className="text-[10px] uppercase text-muted-foreground">
            Prompt template
          </Label>
          <pre className="font-mono text-[11px] whitespace-pre-wrap break-words bg-background border rounded p-2 max-h-48 overflow-y-auto">
            {agent.prompt_template}
          </pre>
          {promptShort ? (
            <p className="text-[10px] text-muted-foreground italic">
              {agent.prompt_template.length} chars
            </p>
          ) : null}
        </div>

        <div className="space-y-1">
          <Label className="text-[10px] uppercase text-muted-foreground">
            runtime_config
          </Label>
          <pre className="font-mono text-[11px] whitespace-pre-wrap break-words bg-background border rounded p-2 max-h-40 overflow-y-auto">
            {JSON.stringify(agent.runtime_config, null, 2)}
          </pre>
        </div>

        <SchemaList label="input_schema" fields={agent.input_schema} />
        <SchemaList label="output_schema" fields={agent.output_schema} />

        {agent.constraints.length > 0 ? (
          <SchemaList label="constraints" fields={agent.constraints} />
        ) : null}
      </div>
    </details>
  );
}

function SchemaList({ label, fields }: { label: string; fields: string[] }) {
  return (
    <div className="space-y-1">
      <Label className="text-[10px] uppercase text-muted-foreground">{label}</Label>
      {fields.length === 0 ? (
        <p className="italic text-muted-foreground text-[11px]">
          (empty — legacy mode, full state visible)
        </p>
      ) : (
        <div className="flex flex-wrap gap-1">
          {fields.map((f) => (
            <Badge key={f} variant="outline" className="font-mono text-[10px]">
              {f}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

interface StateAfterNodeFields {
  fields: string[];
  hasLegacyAncestor: boolean;
  legacyNodeIds: string[];
}

function StateAfterNodeView({ fields }: { fields: StateAfterNodeFields }) {
  if (fields.fields.length === 0 && !fields.hasLegacyAncestor) {
    return (
      <p className="italic text-muted-foreground text-[11px]">
        Nothing declared — entry node with no upstream writers and no
        ``output_schema`` of its own.
      </p>
    );
  }
  return (
    <div className="space-y-1.5">
      {fields.fields.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {fields.fields.map((f) => (
            <Badge key={f} variant="outline" className="font-mono text-[10px]">
              {f}
            </Badge>
          ))}
        </div>
      ) : null}
      {fields.hasLegacyAncestor ? (
        <p className="text-[11px] italic text-muted-foreground">
          + any state field a downstream node may read — at least one
          upstream agent ({fields.legacyNodeIds.join(", ")}) is in
          legacy mode (empty <code className="font-mono">output_schema</code>),
          so its writes aren&apos;t declared.
        </p>
      ) : null}
    </div>
  );
}

// BFS upstream from target collecting output_schema. Legacy ancestors (empty output_schema) are flagged separately since we can't enumerate their writes.
function computeStateAfterNode(
  targetNodeId: string,
  allNodes: PipelineNode[],
  allEdges: PipelineEdge[],
  agents: Agent[],
): StateAfterNodeFields {
  const agentById = new Map(agents.map((a) => [a.id, a] as const));
  const reverseAdj = new Map<string, string[]>();
  for (const edge of allEdges) {
    if (!reverseAdj.has(edge.target)) reverseAdj.set(edge.target, []);
    reverseAdj.get(edge.target)!.push(edge.source);
  }

  const visited = new Set<string>([targetNodeId]);
  const queue = [targetNodeId];
  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const upstream of reverseAdj.get(cur) ?? []) {
      if (upstream.startsWith("__")) continue;
      if (!visited.has(upstream)) {
        visited.add(upstream);
        queue.push(upstream);
      }
    }
  }

  const fieldUnion = new Set<string>();
  const legacyNodeIds: string[] = [];
  for (const nodeId of visited) {
    const node = allNodes.find((n) => n.id === nodeId);
    if (!node) continue;
    const agent = agentById.get(node.agent_id);
    if (!agent) continue;
    if (agent.output_schema.length === 0) {
      legacyNodeIds.push(nodeId);
      continue;
    }
    for (const f of agent.output_schema) fieldUnion.add(f);
  }

  return {
    fields: Array.from(fieldUnion).sort(),
    hasLegacyAncestor: legacyNodeIds.length > 0,
    legacyNodeIds: legacyNodeIds.sort(),
  };
}

interface EdgePanelProps {
  edge: DesignerEdge;
  annotation: EdgeAnnotation;
  onUpdateCondition: (condition: EdgeCondition | null) => void;
  onUpdateLabel: (label: string) => void;
  onDelete: (edgeId: string) => void;
}

function EdgePanel({
  edge,
  annotation,
  onUpdateCondition,
  onUpdateLabel,
  onDelete,
}: EdgePanelProps) {
  return (
    <div className="space-y-3">
      <Field label="Edge ID">
        <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
          {edge.id}
        </code>
      </Field>

      <div className="grid grid-cols-2 gap-2">
        <Field label="Source">
          <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
            {edge.source}
          </code>
        </Field>
        <Field label="Target">
          <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
            {edge.target}
          </code>
        </Field>
      </div>

      <Field label="Fields flowing through">
        {annotation.warning ? (
          <p className="text-xs text-destructive">
            Downstream node declares inputs but none match the upstream&apos;s
            outputs. Either widen the source&apos;s output_schema or narrow
            the target&apos;s input_schema.
          </p>
        ) : annotation.unknown ? (
          <p className="text-xs italic text-muted-foreground">
            Agent lookup failed — the agents list may still be loading,
            or one of the referenced agents has been archived/deleted.
          </p>
        ) : annotation.fields.length === 0 ? (
          <p className="text-xs italic text-muted-foreground">
            No declared field flow — at least one endpoint is in legacy
            mode (empty schema).
          </p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {annotation.fields.map((f) => (
              <Badge key={f} variant="outline" className="font-mono text-[10px]">
                {f}
              </Badge>
            ))}
          </div>
        )}
      </Field>

      <Field label="Label (optional)">
        <Input
          value={edge.label ?? ""}
          onChange={(e) => onUpdateLabel(e.target.value)}
          placeholder="e.g. on success"
          className="h-8 text-xs"
        />
      </Field>

      <Field label="Condition">
        <ConditionBuilder
          condition={edge.condition ?? null}
          onChange={onUpdateCondition}
        />
      </Field>

      <div className="pt-2 border-t">
        <Button
          type="button"
          variant="destructive"
          size="sm"
          onClick={() => onDelete(edge.id)}
          className="w-full"
        >
          <Trash2 className="h-3 w-3 mr-1" />
          Delete edge
        </Button>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}
