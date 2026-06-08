"use client";

/**
 * Designer inspector orchestrator (audit D1 slice 4).
 *
 * Post-split this file just owns the selection-type discriminated
 * union and routes to the appropriate panel. Presentational
 * pieces live under ``components/designer/inspector/``:
 *
 * - ``shared.tsx``           — ``Field`` label-over-content stack
 * - ``node-panel.tsx``       — node selection panel + agent
 *                              details + schema lists + bundled-
 *                              agent fallback
 * - ``edge-panel.tsx``       — edge selection panel + condition
 *                              builder hookup
 * - ``state-after-node.tsx`` — DAG-walk computation + presentation
 *                              of declared state visible after a
 *                              node
 *
 * Pre-split this file was 545 LOC; post-split it stays under 80.
 */

import type { Agent, EdgeCondition } from "@/lib/api/types";
import type { EdgeAnnotation } from "@/lib/edge-annotations";

import { EdgePanel } from "./inspector/edge-panel";
import { NodePanel } from "./inspector/node-panel";
import { BackendProfilePanel } from "./inspector/backend-profile-editor";
import type { BackendProfiles } from "@/lib/backend-profile-assignments";
import type { DesignerEdge, DesignerNode } from "./types";


type Selection =
  | { kind: "node"; node: DesignerNode }
  | { kind: "edge"; edge: DesignerEdge; annotation: EdgeAnnotation }
  | { kind: "none" };


interface InspectorProps {
  selection: Selection;
  agents: Agent[];
  // Full DAG snapshot — Node panel walks back through edges to
  // compute cumulative upstream output_schema.
  allNodes: DesignerNode[];
  allEdges: DesignerEdge[];
  entryPoint: string;
  onSetEntryPoint: (nodeId: string) => void;
  onDeleteNode: (nodeId: string) => void;
  onDeleteEdge: (edgeId: string) => void;
  onUpdateEdgeCondition: (edgeId: string, condition: EdgeCondition | null) => void;
  onUpdateEdgeLabel: (edgeId: string, label: string) => void;
  backendProfiles: Record<string, unknown> | null;
  onBackendProfilesChange: (next: Record<string, unknown>) => void;
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
          <div className="space-y-4">
            <p className="text-xs text-muted-foreground">Click a node or edge to inspect.</p>
            <div className="border-t pt-4">
              <BackendProfilePanel
                nodes={props.allNodes.map((n) => ({ id: n.id, label: n.id }))}
                backendProfiles={props.backendProfiles as BackendProfiles | null}
                onChange={props.onBackendProfilesChange}
              />
            </div>
          </div>
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
            backendProfiles={props.backendProfiles as BackendProfiles | null}
            onBackendProfilesChange={props.onBackendProfilesChange}
          />
        )}
        {props.selection.kind === "edge" && (
          <EdgePanel
            edge={props.selection.edge}
            annotation={props.selection.annotation}
            onUpdateCondition={(c) =>
              props.onUpdateEdgeCondition(
                props.selection.kind === "edge" ? props.selection.edge.id : "",
                c,
              )
            }
            onUpdateLabel={(l) =>
              props.onUpdateEdgeLabel(
                props.selection.kind === "edge" ? props.selection.edge.id : "",
                l,
              )
            }
            onDelete={props.onDeleteEdge}
          />
        )}
      </div>
    </aside>
  );
}
