/**
 * Designer-local domain types — separate from the API DTO so we can carry
 * UI-only fields (selected, dirty markers, etc.) without polluting Pipeline.
 */

import type { EdgeCondition, PipelineEdge, PipelineNode } from "@/lib/api/types";

// Today these are 1:1 with their API counterparts; the dedicated aliases
// give us a single hook to attach designer-only fields (selected, dirty,
// etc.) later without touching every call site.
export type DesignerNode = PipelineNode;
export type DesignerEdge = PipelineEdge;

export interface DesignerState {
  name: string;
  description: string;
  entryPoint: string;
  nodes: DesignerNode[];
  edges: DesignerEdge[];
}

export interface PipelineFormPayload {
  name: string;
  description: string;
  schema_version: "langgraph/1.0";
  state_schema_ref: string;
  entry_point: string;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  defaults: {
    max_attempts: number;
    budget_limit_usd: number;
    approval_required_nodes: string[];
  };
}

export const DEFAULT_DEFAULTS = {
  max_attempts: 3,
  budget_limit_usd: 5.0,
  approval_required_nodes: [],
};

export const STATE_SCHEMA_REF = "PipelineState.v1";

export type ConditionDraft = EdgeCondition | null;
