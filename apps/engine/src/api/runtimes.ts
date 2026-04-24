import type { FastifyInstance } from "fastify";
import type { RuntimeRegistry } from "@dap/runtimes";

export async function registerRuntimeRoutes(
  app: FastifyInstance,
  registry: RuntimeRegistry,
): Promise<void> {
  app.get("/runtimes", async () => {
    const adapters = registry.list();
    return adapters.map((a) => ({
      id: a.id,
      displayName: a.displayName,
      kind: a.kind,
    }));
  });

  app.get<{ Params: { id: string } }>("/runtimes/:id/health", async (request, reply) => {
    const { id } = request.params;
    if (!registry.has(id)) {
      return reply.code(404).send({ error: `Runtime not found: ${id}` });
    }
    const adapter = registry.get(id);
    return adapter.healthcheck();
  });
}
