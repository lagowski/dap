import type { RuntimeAdapter } from "@dap/types";

export class RuntimeRegistry {
  private adapters = new Map<string, RuntimeAdapter>();

  register(adapter: RuntimeAdapter): void {
    if (this.adapters.has(adapter.id)) {
      throw new Error(`Runtime adapter already registered: ${adapter.id}`);
    }
    this.adapters.set(adapter.id, adapter);
  }

  get(id: string): RuntimeAdapter {
    const adapter = this.adapters.get(id);
    if (!adapter) {
      throw new Error(`Runtime adapter not found: ${id}`);
    }
    return adapter;
  }

  has(id: string): boolean {
    return this.adapters.has(id);
  }

  list(): RuntimeAdapter[] {
    return Array.from(this.adapters.values());
  }
}

export async function createDefaultRegistry(): Promise<RuntimeRegistry> {
  const registry = new RuntimeRegistry();

  const { BashAdapter } = await import("./adapters/bash.js");
  const { HttpAdapter } = await import("./adapters/http.js");
  const { ApiCallAdapter } = await import("./adapters/api-call.js");
  const { ClaudeCodeAdapter } = await import("./adapters/claude-code.js");
  const { GeminiCliAdapter } = await import("./adapters/gemini-cli.js");
  const { CodexAdapter } = await import("./adapters/codex.js");
  const { AiderAdapter } = await import("./adapters/aider.js");

  registry.register(new BashAdapter());
  registry.register(new HttpAdapter());
  registry.register(new ApiCallAdapter());
  registry.register(new ClaudeCodeAdapter());
  registry.register(new GeminiCliAdapter());
  registry.register(new CodexAdapter());
  registry.register(new AiderAdapter());

  return registry;
}
