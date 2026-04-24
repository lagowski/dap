import type { RuntimeTask, RuntimeResult, HealthStatus, RuntimeKind } from "@dap/types";
import { BaseAdapter } from "./base.js";

export class HttpAdapter extends BaseAdapter {
  readonly id = "http";
  readonly displayName = "HTTP endpoint";
  readonly kind: RuntimeKind = "http";

  async healthcheck(): Promise<HealthStatus> {
    return { available: true };
  }

  async execute(_task: RuntimeTask): Promise<RuntimeResult> {
    this.notImplemented("execute");
  }
}
