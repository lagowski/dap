#!/usr/bin/env node
import { createEngine } from "./index.js";

async function main(): Promise<void> {
  const engine = await createEngine({
    dbPath: process.env["DAP_DB_PATH"] ?? "./.dap/state.db",
    host: process.env["DAP_ENGINE_HOST"] ?? "127.0.0.1",
    port: Number(process.env["DAP_ENGINE_PORT"] ?? 7333),
  });

  const address = await engine.start();
  engine.app.log.info(`@dap/engine listening at ${address}`);

  const shutdown = async (signal: string): Promise<void> => {
    engine.app.log.info(`Received ${signal}, shutting down...`);
    await engine.stop();
    process.exit(0);
  };

  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((err) => {
  console.error("Engine failed to start:", err);
  process.exit(1);
});
