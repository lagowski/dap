import type { FastifyInstance } from "fastify";

export async function registerHealthRoutes(app: FastifyInstance): Promise<void> {
  app.get("/health", async () => ({
    status: "ok",
    service: "@dap/engine",
    version: "0.0.1",
    timestamp: new Date().toISOString(),
  }));
}
