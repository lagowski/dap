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
  overrides: {
    name?: string;
    description?: string;
    sleepSeconds?: number;
    gateNodes?: string[];
  } = {},
): Promise<Pipeline> {
  // Pipelines need at least one node that references an existing agent —
  // seed an agent first, then a single-node pipeline pointing at it. This
  // keeps the test independent of which agents the user happens to have.
  // For lifecycle tests that need a "still running" window, pass
  // `sleepSeconds` to swap in a `sleep N` bash command instead of the
  // default `echo ok` — the run then stays in `running` state long
  // enough for the UI to surface Pause / Abort buttons.
  const agent =
    overrides.sleepSeconds != null
      ? await createAgent(request, {
          prompt_template: `<agent_prompt><command>sleep ${overrides.sleepSeconds}</command></agent_prompt>`,
        })
      : await createAgent(request);

  // Validator requires every node to have a path to the END sentinel
  // ("__end__"), so a single-node pipeline still needs one explicit
  // terminator edge or the POST returns 422 "Node 'n1' has no path to END".
  const payload: Record<string, unknown> = {
    name: overrides.name ?? uniquePipelineName(),
    description: overrides.description ?? '',
    schema_version: 'langgraph/1.0',
    state_schema_ref: 'PipelineState.v1',
    entry_point: 'n1',
    nodes: [{ id: 'n1', agent_id: agent.id }],
    edges: [{ id: 'e1', source: 'n1', target: '__end__' }],
  };
  if (overrides.gateNodes && overrides.gateNodes.length > 0) {
    // PipelineDefaults.approval_required_nodes drives the langgraph
    // runner's `interrupt_before` — the run pauses BEFORE executing
    // any listed node, hitting final_status="paused" for the
    // human-in-loop approval flow tested by gate specs.
    payload.defaults = { approval_required_nodes: overrides.gateNodes };
  }
  const response = await request.post('/api/pipelines', { data: payload });
  if (!response.ok()) {
    throw new Error(
      `Failed to seed pipeline (status ${response.status()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as Pipeline;
}
