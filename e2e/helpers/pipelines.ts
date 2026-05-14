import type { APIRequestContext } from '@playwright/test';
import { createAgent } from './agents';

export interface Pipeline {
  id: string;
  name: string;
  description: string;
  version: number;
  schema_version: string;
  state_schema_ref: string;
  entry_point: string;
  nodes: Array<{ id: string; agent_id: string }>;
  edges: unknown[];
  is_active: boolean;
}

export function uniquePipelineName(prefix = 'e2e-pipeline'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export async function createPipeline(
  request: APIRequestContext,
  overrides: { name?: string; description?: string } = {},
): Promise<Pipeline> {
  // Pipelines need at least one node that references an existing agent —
  // seed an agent first, then a single-node pipeline pointing at it. This
  // keeps the test independent of which agents the user happens to have.
  const agent = await createAgent(request);

  // Validator requires every node to have a path to the END sentinel
  // ("__end__"), so a single-node pipeline still needs one explicit
  // terminator edge or the POST returns 422 "Node 'n1' has no path to END".
  const payload = {
    name: overrides.name ?? uniquePipelineName(),
    description: overrides.description ?? '',
    schema_version: 'langgraph/1.0',
    state_schema_ref: 'PipelineState.v1',
    entry_point: 'n1',
    nodes: [{ id: 'n1', agent_id: agent.id }],
    edges: [{ id: 'e1', source: 'n1', target: '__end__' }],
  };
  const response = await request.post('/api/pipelines', { data: payload });
  if (!response.ok()) {
    throw new Error(
      `Failed to seed pipeline (status ${response.status()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as Pipeline;
}
