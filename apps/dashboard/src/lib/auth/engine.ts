/**
 * Server-side engine URL resolution for route handlers + middleware.
 *
 * The browser never talks to the engine directly after Phase B — it
 * talks to the dashboard, and the dashboard proxies. So the engine
 * URL is a *server-only* env var (no ``NEXT_PUBLIC_`` prefix).
 *
 * For backwards compatibility during the transition we still read
 * ``NEXT_PUBLIC_DAP_ENGINE_URL`` if the new variable is unset — that
 * keeps existing dev setups working without a config change.
 */

export function getEngineUrl(): string {
  return (
    process.env.DAP_ENGINE_URL ??
    process.env.NEXT_PUBLIC_DAP_ENGINE_URL ??
    "http://127.0.0.1:7333"
  );
}
