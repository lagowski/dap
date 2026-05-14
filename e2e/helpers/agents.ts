import type { APIRequestContext } from '@playwright/test';

export interface AgentCreatePayload {
  name: string;
  role: string;
  runtime_id: string;
  runtime_config?: Record<string, unknown>;
  prompt_template: string;
  input_schema?: string[];
  output_schema?: string[];
  constraints?: string[];
  budget_limit_usd?: number | null;
  timeout_ms?: number;
}

export interface Agent extends Required<Omit<AgentCreatePayload, 'budget_limit_usd'>> {
  id: string;
  version: number;
  budget_limit_usd: number | null;
  created_at: string;
  updated_at: string;
  is_active: boolean;
}

// `bash` runtime accepts an empty config; the engine extracts the command
// from a <command> tag in the prompt template. Cheapest choice for tests —
// no LLM provider required, and embedding <command> keeps the seeded agent
// valid if anything ever tries to execute it (the CRUD specs don't, but
// other consumers of this helper might).
export function defaultAgentPayload(overrides: Partial<AgentCreatePayload> = {}): AgentCreatePayload {
  return {
    name: uniqueAgentName(),
    role: 'implementer',
    runtime_id: 'bash',
    runtime_config: {},
    prompt_template: '<agent_prompt><command>echo ok</command></agent_prompt>',
    input_schema: [],
    output_schema: [],
    constraints: [],
    timeout_ms: 60_000,
    ...overrides,
  };
}

export function uniqueAgentName(prefix = 'e2e-agent'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export async function createAgent(
  request: APIRequestContext,
  overrides: Partial<AgentCreatePayload> = {},
): Promise<Agent> {
  const payload = defaultAgentPayload(overrides);
  const response = await request.post('/api/agents', { data: payload });
  if (!response.ok()) {
    throw new Error(
      `Failed to seed agent (status ${response.status()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as Agent;
}
