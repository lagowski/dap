import Fastify, { type FastifyInstance } from "fastify";
import cors from "@fastify/cors";
import { createDefaultRegistry } from "@dap/runtimes";
import { openDb, type Db } from "./persistence/db.js";
import { registerHealthRoutes } from "./api/health.js";
import { registerRuntimeRoutes } from "./api/runtimes.js";

export interface EngineOptions {
  dbPath: string;
  host?: string;
  port?: number;
}

export interface Engine {
  app: FastifyInstance;
  db: Db;
  start(): Promise<string>;
  stop(): Promise<void>;
}

export async function createEngine(options: EngineOptions): Promise<Engine> {
  const { db, close: closeDb } = openDb({ path: options.dbPath });
  const registry = await createDefaultRegistry();

  const app = Fastify({
    logger: { level: "info" },
  });

  await app.register(cors, { origin: true });
  await registerHealthRoutes(app);
  await registerRuntimeRoutes(app, registry);

  return {
    app,
    db,
    async start(): Promise<string> {
      const address = await app.listen({
        host: options.host ?? "127.0.0.1",
        port: options.port ?? 7333,
      });
      return address;
    },
    async stop(): Promise<void> {
      await app.close();
      closeDb();
    },
  };
}
