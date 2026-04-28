/**
 * Edge annotations for pipeline graphs (#62).
 *
 * Each edge in a DAP pipeline carries a *contract* between the upstream
 * agent's outputs and the downstream agent's inputs: the fields the
 * downstream node will actually consume from the upstream node's
 * production. Surfacing this on the graph turns opaque arrows into a
 * readable data-flow story.
 *
 * Used by both the designer (live editing) and the run viewer
 * (read-only) — same render path, same chip data.
 */

import type { Agent, PipelineEdge, PipelineNode } from "@/lib/api/types";

export interface EdgeAnnotation {
  /** Fields flowing through this edge — intersection of source outputs and target inputs. */
  fields: string[];
  /**
   * The target declares inputs but none of them match the source's
   * outputs. Almost always a wiring bug — runtime would feed the
   * target with stale state instead of fresh upstream output.
   */
  warning: boolean;
}

/**
 * Compute the annotation for a single edge.
 *
 * - Both endpoints in legacy mode (empty schemas) → empty annotation,
 *   no warning. Falls back to v0.4 behaviour where the chip just
 *   doesn't render anything.
 * - Target in legacy mode (empty input_schema) → no warning either,
 *   even if source declares outputs — the runtime will still pass the
 *   full state to a non-scoped template.
 * - Target declares inputs, source declares outputs, intersection
 *   empty → warning. Designer renders this in red.
 */
export function computeEdgeAnnotation(
  source: Agent | undefined,
  target: Agent | undefined,
): EdgeAnnotation {
  if (source === undefined || target === undefined) {
    return { fields: [], warning: false };
  }
  const targetInputs = target.input_schema ?? [];
  const sourceOutputs = new Set(source.output_schema ?? []);
  const fields = targetInputs.filter((f) => sourceOutputs.has(f));
  const warning =
    targetInputs.length > 0 &&
    sourceOutputs.size > 0 &&
    fields.length === 0;
  return { fields, warning };
}

/**
 * Resolve an agent for a node by walking the pipeline's nodes list and
 * looking up the agent_id in the agents map. Returns undefined when
 * the agent is unknown (deleted, or not yet loaded).
 */
export function agentForNode(
  nodeId: string,
  nodes: readonly PipelineNode[],
  agentsById: ReadonlyMap<string, Agent>,
): Agent | undefined {
  const node = nodes.find((n) => n.id === nodeId);
  if (node === undefined) return undefined;
  return agentsById.get(node.agent_id);
}

/**
 * Edge label format used by both the designer and the run viewer so the
 * chip looks identical in both places.
 *
 * - 0 fields, no warning, no condition → no label at all.
 * - 1-2 fields → ``{f1, f2}``.
 * - 3+ fields → ``{n fields}`` (full list rendered via title attribute /
 *   inspector when the user clicks the edge).
 * - Warning → ``⚠ no shared fields``.
 * - Conditional edge → ``if`` is rendered separately by the caller and
 *   prepended via ``formatEdgeLabel``.
 */
export function formatAnnotationLabel(annotation: EdgeAnnotation): string {
  if (annotation.warning) {
    return "⚠ no shared fields";
  }
  const { fields } = annotation;
  if (fields.length === 0) return "";
  if (fields.length <= 2) {
    return `{${fields.join(", ")}}`;
  }
  return `{${fields.length} fields}`;
}

/**
 * Combine the optional ``if`` marker (for conditional edges) with the
 * annotation chip into a single edge label string.
 */
export function formatEdgeLabel(
  annotation: EdgeAnnotation,
  hasCondition: boolean,
): string | undefined {
  const chip = formatAnnotationLabel(annotation);
  const parts: string[] = [];
  if (hasCondition) parts.push("if");
  if (chip) parts.push(chip);
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

/**
 * Hover-tooltip text — full field list even when the label is the
 * compact "{n fields}" form. Empty annotation returns undefined so the
 * caller skips setting a title attribute.
 */
export function annotationTooltip(annotation: EdgeAnnotation): string | undefined {
  if (annotation.warning) {
    return "Downstream node declares inputs but none match the upstream's outputs.";
  }
  if (annotation.fields.length === 0) return undefined;
  return `Fields flowing through: ${annotation.fields.join(", ")}`;
}

/**
 * Convenience: compute annotation for every edge given the pipeline's
 * nodes, edges, and an agents map. Used by both the designer
 * (re-computed on every edit) and the run viewer (computed once when
 * the run loads).
 */
export function annotateEdges(
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  agents: readonly Agent[],
): Map<string, EdgeAnnotation> {
  const agentsById = new Map(agents.map((a) => [a.id, a]));
  const out = new Map<string, EdgeAnnotation>();
  for (const edge of edges) {
    const source = agentForNode(edge.source, nodes, agentsById);
    const target = agentForNode(edge.target, nodes, agentsById);
    out.set(edge.id, computeEdgeAnnotation(source, target));
  }
  return out;
}
