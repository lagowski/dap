import type { RuntimeTask, RuntimeResult, HealthStatus, RuntimeKind } from "@dap/types";
import { BaseAdapter } from "./base.js";

export class ClaudeCodeAdapter extends BaseAdapter {
  readonly id = "claude-code";
  readonly displayName = "Claude Code CLI";
  readonly kind: RuntimeKind = "cli";

  async healthcheck(): Promise<HealthStatus> {
    return { available: false, missing: ["claude binary (install: https://docs.claude.com/claude-code)"] };
  }

  async execute(_task: RuntimeTask): Promise<RuntimeResult> {
    this.notImplemented("execute");
  }
}
