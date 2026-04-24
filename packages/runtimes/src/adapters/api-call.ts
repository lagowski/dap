import type { RuntimeTask, RuntimeResult, HealthStatus, RuntimeKind } from "@dap/types";
import { BaseAdapter } from "./base.js";

export class ApiCallAdapter extends BaseAdapter {
  readonly id = "api-call";
  readonly displayName = "Direct LLM API call (SDK)";
  readonly kind: RuntimeKind = "api";

  async healthcheck(): Promise<HealthStatus> {
    return { available: true };
  }

  async execute(_task: RuntimeTask): Promise<RuntimeResult> {
    this.notImplemented("execute");
  }
}
