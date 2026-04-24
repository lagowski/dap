import type { RuntimeTask, RuntimeResult, HealthStatus, RuntimeKind } from "@dap/types";
import { BaseAdapter } from "./base.js";

export class CodexAdapter extends BaseAdapter {
  readonly id = "codex";
  readonly displayName = "OpenAI Codex CLI";
  readonly kind: RuntimeKind = "cli";

  async healthcheck(): Promise<HealthStatus> {
    return { available: false, missing: ["codex binary"] };
  }

  async execute(_task: RuntimeTask): Promise<RuntimeResult> {
    this.notImplemented("execute");
  }
}
