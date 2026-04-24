import type { RuntimeTask, RuntimeResult, HealthStatus, RuntimeKind } from "@dap/types";
import { BaseAdapter } from "./base.js";

export class GeminiCliAdapter extends BaseAdapter {
  readonly id = "gemini-cli";
  readonly displayName = "Gemini CLI";
  readonly kind: RuntimeKind = "cli";

  async healthcheck(): Promise<HealthStatus> {
    return { available: false, missing: ["gemini binary"] };
  }

  async execute(_task: RuntimeTask): Promise<RuntimeResult> {
    this.notImplemented("execute");
  }
}
