"use client";

/**
 * "State after this node" computation + presentation (audit D1
 * split).
 *
 * Walks the pipeline DAG backwards from a target node, unions the
 * declared ``output_schema`` of every upstream node, and surfaces
 * the legacy/python-func distinctions so the UI can explain why
 * fields are or aren't declared (#229).
 *
 * The pure computation lives next to the view so a future change to
 * one stays adjacent to the other — the view is essentially a
 * presentation of the ``StateAfterNodeFields`` shape, and they're
 * meaningless without each other.
 */

import { Badge } from "@/components/ui/badge";
import type { Agent, PipelineEdge, PipelineNode } from "@/lib/api/types";


export interface StateAfterNodeFields {
  fields: string[];
  selfIsLegacy: boolean;
  selfIsPythonFunc: boolean;
  legacyAncestorIds: string[];
}


export function StateAfterNodeView({ fields }: { fields: StateAfterNodeFields }) {
  const hasUpstreamLegacy = fields.legacyAncestorIds.length > 0;
  // python-func nodes read/write arbitrary PipelineState — show passthrough
  // note regardless of whether upstream nodes declared specific fields (#229).
  if (fields.selfIsPythonFunc) {
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
        <p className="italic text-muted-foreground text-[11px]">
          python-func runtime — full{" "}
          <code className="font-mono">PipelineState</code> passthrough
        </p>
      </div>
    );
  }
  if (fields.fields.length === 0 && !hasUpstreamLegacy && !fields.selfIsLegacy) {
    return (
      <p className="italic text-muted-foreground text-[11px]">
        Nothing declared — no upstream writers and this node has no{" "}
        <code className="font-mono">output_schema</code>.
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
      {hasUpstreamLegacy ? (
        <p className="text-[11px] italic text-muted-foreground">
          + any state field a downstream node may read —{" "}
          {fields.legacyAncestorIds.length > 1
            ? `upstream agents (${fields.legacyAncestorIds.join(", ")}) are`
            : `upstream agent (${fields.legacyAncestorIds[0]}) is`}{" "}
          in legacy mode (empty <code className="font-mono">output_schema</code>),
          so their writes aren&apos;t declared.
        </p>
      ) : null}
      {fields.selfIsLegacy ? (
        <p className="text-[11px] italic text-muted-foreground">
          This node is in legacy mode (empty{" "}
          <code className="font-mono">output_schema</code>) — its own writes
          aren&apos;t declared.
        </p>
      ) : null}
    </div>
  );
}


/**
 * BFS upstream from ``targetNodeId`` collecting the union of
 * declared ``output_schema`` across all ancestor agents.
 *
 * Self-legacy and upstream-legacy are tracked separately so the UI
 * can word them differently — operators care more about "this node
 * itself doesn't declare its writes" vs. "an upstream node doesn't".
 * ``python-func`` agents are intentionally not marked legacy: they
 * do a full ``PipelineState`` passthrough (#229), which is a
 * different semantic from a legacy agent that simply omitted the
 * schema.
 */
export function computeStateAfterNode(
  targetNodeId: string,
  allNodes: PipelineNode[],
  allEdges: PipelineEdge[],
  agents: Agent[],
): StateAfterNodeFields {
  const agentById = new Map(agents.map((a) => [a.id, a] as const));
  const nodeById = new Map(allNodes.map((n) => [n.id, n] as const));
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
  const legacyAncestorIds: string[] = [];
  let selfIsLegacy = false;
  let selfIsPythonFunc = false;
  for (const nodeId of visited) {
    const node = nodeById.get(nodeId);
    if (!node) continue;
    const agent = agentById.get(node.agent_id);
    if (!agent) continue;
    if (agent.runtime_id === "python-func") {
      if (nodeId === targetNodeId) {
        selfIsPythonFunc = true;
      }
      continue;
    }
    if (agent.output_schema.length === 0) {
      if (nodeId === targetNodeId) {
        selfIsLegacy = true;
      } else {
        legacyAncestorIds.push(nodeId);
      }
      continue;
    }
    for (const f of agent.output_schema) fieldUnion.add(f);
  }

  return {
    fields: Array.from(fieldUnion).sort(),
    selfIsLegacy,
    selfIsPythonFunc,
    legacyAncestorIds: legacyAncestorIds.sort(),
  };
}
