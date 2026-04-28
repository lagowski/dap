"use client";

import { Trash2 } from "lucide-react";
import type { Agent, EdgeCondition } from "@/lib/api/types";
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
  entryPoint: string;
  onSetEntryPoint: (nodeId: string) => void;
  onDelete: (nodeId: string) => void;
}

function NodePanel({
  node,
  agents,
  entryPoint,
  onSetEntryPoint,
  onDelete,
}: NodePanelProps) {
  const agent = agents.find((a) => a.id === node.agent_id);
  const isEntry = entryPoint === node.id;

  return (
    <div className="space-y-3">
      <Field label="Node ID">
        <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
          {node.id}
        </code>
      </Field>

      <Field label="Agent">
        {agent ? (
          <div className="text-sm space-y-1">
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
          </div>
        ) : (
          <span className="text-xs text-destructive">
            Agent not found: {node.agent_id}
          </span>
        )}
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
