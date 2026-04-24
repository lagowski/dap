export type RuntimeKind = "cli" | "api" | "shell" | "http";

export interface RuntimeTask {
  executionId: string;
  promptXml: string;
  workingDirectory: string;
  allowedFiles?: string[];
  allowedTools?: string[];
  timeoutMs: number;
  budgetUsd?: number;
  runtimeConfig: Record<string, unknown>;
  context: {
    inputFields: Record<string, unknown>;
  };
}

export interface RuntimeResult {
  success: boolean;
  output: string;
  structured?: Record<string, unknown>;
  filesChanged: string[];
  tokensUsed?: number;
  costUsd?: number;
  durationMs: number;
  errors: string[];
}

export interface HealthStatus {
  available: boolean;
  version?: string;
  missing?: string[];
}

export interface RuntimeAdapter {
  readonly id: string;
  readonly displayName: string;
  readonly kind: RuntimeKind;

  healthcheck(): Promise<HealthStatus>;
  execute(task: RuntimeTask): Promise<RuntimeResult>;
  abort?(executionId: string): Promise<void>;
}
