import type { RuntimeAdapter, RuntimeTask, RuntimeResult, HealthStatus, RuntimeKind } from "@dap/types";

export abstract class BaseAdapter implements RuntimeAdapter {
  abstract readonly id: string;
  abstract readonly displayName: string;
  abstract readonly kind: RuntimeKind;

  abstract healthcheck(): Promise<HealthStatus>;
  abstract execute(task: RuntimeTask): Promise<RuntimeResult>;

  protected notImplemented(method: string): never {
    throw new Error(`${this.id}.${method}() not implemented yet — scheduled for F3/F9`);
  }
}
