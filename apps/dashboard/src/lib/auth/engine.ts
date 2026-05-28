/**
 * Server-side engine URL resolution for route handlers + middleware.
 *
 * The browser never talks to the engine directly — it talks to the
 * dashboard, and the dashboard proxies. The engine URL is a
 * server-only env var (no ``NEXT_PUBLIC_`` prefix).
 */

export function getEngineUrl(): string {
  return process.env.DAP_ENGINE_URL ?? "http://127.0.0.1:7333";
}
