import type { RuntimeTask, RuntimeResult, HealthStatus, RuntimeKind } from "@dap/types";
import { BaseAdapter } from "./base.js";

export class AiderAdapter extends BaseAdapter {
  readonly id = "aider";
  readonly displayName = "Aider";
  readonly kind: RuntimeKind = "cli";

  async healthcheck(): Promise<HealthStatus> {
    return { available: false, missing: ["aider binary"] };
  }

  async execute(_task: RuntimeTask): Promise<RuntimeResult> {
    this.notImplemented("execute");
  }
}
