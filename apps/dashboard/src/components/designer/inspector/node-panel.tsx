"use client";

/**
 * Node selection panel for the Inspector (audit D1 split).
 *
 * Shows the node's agent details, computed state after the node,
 * position, and entry-point + delete controls. Sub-components
 * (``BundledAgentFallback``, ``AgentDetailsCollapse``,
 * ``SchemaList``) are co-located here because they're only used
 * from this panel.
 */

import Link from "next/link";
import { ExternalLink, FlaskConical, Pencil, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import type { Agent, PipelineEdge, PipelineNode } from "@/lib/api/types";

import type { DesignerNode } from "../types";

import { Field } from "./shared";
import { StateAfterNodeView, computeStateAfterNode } from "./state-after-node";
import { NodeProfileSelect } from "./backend-profile-editor";
import type { BackendProfiles } from "@/lib/backend-profile-assignments";


interface NodePanelProps {
  node: DesignerNode;
  agents: Agent[];
  allNodes: PipelineNode[];
  allEdges: PipelineEdge[];
  entryPoint: string;
  onSetEntryPoint: (nodeId: string) => void;
  onDelete: (nodeId: string) => void;
  backendProfiles: BackendProfiles | null;
  onBackendProfilesChange: (next: BackendProfiles) => void;
}

const PROMPT_PREVIEW_LIMIT = 240;


export function NodePanel({
  node,
  agents,
  allNodes,
  allEdges,
  entryPoint,
  onSetEntryPoint,
  onDelete,
  backendProfiles,
  onBackendProfilesChange,
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

      <Field label="LLM / backend">
        <NodeProfileSelect
          nodeId={node.id}
          backendProfiles={backendProfiles}
          onChange={onBackendProfilesChange}
        />
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
            {/* For python-func agents, surface the callable_path prominently */}
            {agent.runtime_id === "python-func" &&
            typeof agent.runtime_config?.callable_path === "string" ? (
              <code className="text-[11px] font-mono text-muted-foreground block">
                {agent.runtime_config.callable_path as string}
              </code>
            ) : null}
            <div className="flex gap-1.5 pt-1">
              <Button asChild variant="outline" size="sm" className="flex-1 h-7 text-xs">
                <Link
                  href={`/agents/${agent.id}/edit`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
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
          <BundledAgentFallback agentId={node.agent_id} />
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


/**
 * Shown when the agent UUID lookup fails (e.g., agents list is still loading
 * or the agent was imported as a bundle and hasn't appeared yet). Avoids the
 * red "Agent not found" error for legitimate bundled python-func nodes (#228).
 */
function BundledAgentFallback({ agentId }: { agentId: string }) {
  return (
    <div className="space-y-1">
      <Badge variant="secondary" className="text-[10px]">
        Bundled agent
      </Badge>
      <p className="text-[11px] text-muted-foreground italic">
        Agent not in local list — may be a bundled pipeline agent still loading.
      </p>
      <code className="text-[10px] font-mono text-muted-foreground break-all block">
        {agentId}
      </code>
    </div>
  );
}


function AgentDetailsCollapse({ agent }: { agent: Agent }) {
  const isPromptLong = agent.prompt_template.length > PROMPT_PREVIEW_LIMIT;
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
          {isPromptLong ? (
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

        <SchemaList
          label="input_schema"
          fields={agent.input_schema}
          runtimeId={agent.runtime_id}
        />
        <SchemaList
          label="output_schema"
          fields={agent.output_schema}
          runtimeId={agent.runtime_id}
        />

        {agent.constraints.length > 0 ? (
          <SchemaList label="constraints" fields={agent.constraints} />
        ) : null}
      </div>
    </details>
  );
}


function SchemaList({
  label,
  fields,
  runtimeId,
}: {
  label: string;
  fields: string[];
  runtimeId?: string;
}) {
  const isPythonFunc = runtimeId === "python-func";
  return (
    <div className="space-y-1">
      <Label className="text-[10px] uppercase text-muted-foreground">{label}</Label>
      {fields.length === 0 ? (
        isPythonFunc ? (
          <p className="italic text-muted-foreground text-[11px]">
            python-func runtime — full{" "}
            <code className="font-mono">PipelineState</code> passthrough
          </p>
        ) : (
          <p className="italic text-muted-foreground text-[11px]">
            (empty — legacy mode, full state visible)
          </p>
        )
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
