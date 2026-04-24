import type { RuntimeTask, RuntimeResult, HealthStatus, RuntimeKind } from "@dap/types";
import { BaseAdapter } from "./base.js";

export class BashAdapter extends BaseAdapter {
  readonly id = "bash";
  readonly displayName = "Bash (shell)";
  readonly kind: RuntimeKind = "shell";

  async healthcheck(): Promise<HealthStatus> {
    return { available: true, version: "system" };
  }

  async execute(_task: RuntimeTask): Promise<RuntimeResult> {
    this.notImplemented("execute");
  }
}
