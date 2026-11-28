import type { APIRequestContext } from '@playwright/test';

export interface Project {
  id: string;
  name: string;
  description: string;
  working_directory: string | null;
  repo_url: string | null;
  default_branch: string;
  pipelines: Record<string, string>;
  env_vars: Record<string, string>;
  is_active: boolean;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export type ProjectOverrides = {
  name?: string;
  description?: string;
  default_branch?: string;
};

export function uniqueProjectName(prefix = 'e2e-project'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// Minimum-viable ProjectCreate payload: only `name` is strictly required by
// the server; description / default_branch have defaults. Optional fields
// like working_directory, repo_url, pipelines, env_vars are left at their
// defaults so seeding doesn't trip the per-field validators (e.g. binding
// pipeline ids that don't exist).
export async function createProject(
  request: APIRequestContext,
  overrides: ProjectOverrides = {},
): Promise<Project> {
  const payload = {
    name: overrides.name ?? uniqueProjectName(),
    description: overrides.description ?? '',
    default_branch: overrides.default_branch ?? 'main',
  };
  const response = await request.post('/api/projects', { data: payload });
  if (!response.ok()) {
    throw new Error(
      `Failed to seed project (status ${response.status()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as Project;
}
