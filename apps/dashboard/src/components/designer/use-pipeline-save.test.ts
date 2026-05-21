import { describe, expect, it } from "vitest";

import type { Pipeline, PipelineEdge, PipelineNode } from "@/lib/api/types";
import { buildLayoutUiMetadata, buildPipelinePayload } from "./use-pipeline-save";

const nodes: PipelineNode[] = [
  { id: "n1", agent_id: "a1", position: { x: 10, y: 20 } },
  { id: "n2", agent_id: "a2", position: { x: 30, y: 40 } },
];

const edges: PipelineEdge[] = [{ id: "e1", source: "n1", target: "n2" }];

const initialPipeline = {
  id: "p1",
  name: "Pipeline",
  description: "",
  version: 3,
  schema_version: "langgraph/1.0",
  state_schema_ref: "PipelineState.v1",
  entry_point: "n1",
  nodes,
  edges,
  defaults: {
    max_attempts: 3,
    budget_limit_usd: 5,
    approval_required_nodes: [],
  },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  is_active: true,
  ui_metadata: {
    custom: { keep: true },
    edge_waypoints: { e1: [{ x: 15, y: 25 }] },
  },
} satisfies Pipeline;

describe("buildLayoutUiMetadata", () => {
  it("snapshots node positions and valid viewport while preserving existing keys", () => {
    const metadata = buildLayoutUiMetadata({
      designerNodes: nodes,
      existingUiMetadata: initialPipeline.ui_metadata,
      viewport: { x: -100, y: 80, zoom: 0.75 },
    });

    expect(metadata).toEqual({
      custom: { keep: true },
      edge_waypoints: { e1: [{ x: 15, y: 25 }] },
      node_positions: {
        n1: { x: 10, y: 20 },
        n2: { x: 30, y: 40 },
      },
      viewport: { x: -100, y: 80, zoom: 0.75 },
    });
  });

  it("omits invalid viewport values", () => {
    const metadata = buildLayoutUiMetadata({
      designerNodes: nodes,
      viewport: { x: 0, y: 0, zoom: 10 },
    });

    expect(metadata).toEqual({
      node_positions: {
        n1: { x: 10, y: 20 },
        n2: { x: 30, y: 40 },
      },
    });
  });
});

describe("buildPipelinePayload", () => {
  it("includes viewport and keeps existing ui_metadata keys in manual save payload", () => {
    const payload = buildPipelinePayload({
      name: "Pipeline",
      description: "Updated",
      entryPoint: "n1",
      designerNodes: nodes,
      designerEdges: edges,
      initialPipeline,
      viewport: { x: 1, y: 2, zoom: 1.25 },
    });

    expect(payload.ui_metadata).toMatchObject({
      custom: { keep: true },
      edge_waypoints: { e1: [{ x: 15, y: 25 }] },
      viewport: { x: 1, y: 2, zoom: 1.25 },
    });
    expect(payload.ui_metadata?.node_positions).toEqual({
      n1: { x: 10, y: 20 },
      n2: { x: 30, y: 40 },
    });
  });
});
