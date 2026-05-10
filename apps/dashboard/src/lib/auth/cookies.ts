/**
 * Cookie helpers for the dashboard ↔ engine auth proxy.
 *
 * The browser never sees the raw JWT — it lives in an httpOnly cookie
 * set by Next.js route handlers (see ``app/api/auth/login/route.ts``).
 * Every outgoing request from a route handler / middleware picks the
 * JWT up via :func:`getJwtCookie` and forwards it as a Bearer header
 * to the engine.
 */

const COOKIE_NAME = "dap-jwt";

/** Cookie name used everywhere — single source of truth. */
export const JWT_COOKIE_NAME = COOKIE_NAME;

/**
 * Build the ``Set-Cookie`` header value for the JWT cookie.
 *
 * - ``HttpOnly`` so JS can't read it (XSS-resistant).
 * - ``SameSite=Lax`` allows top-level navigation from OAuth providers
 *   to land back here with the cookie attached.
 * - ``Secure`` only in production (``NODE_ENV==='production'``) so
 *   local-dev HTTP still works without manual flag flipping.
 * - ``Max-Age`` is the engine's access-token TTL; refresh tokens land
 *   in a separate scheme later.
 */
export function buildJwtCookieHeader(jwt: string, maxAgeSeconds: number): string {
  const flags = [
    `${COOKIE_NAME}=${jwt}`,
    "HttpOnly",
    "SameSite=Lax",
    "Path=/",
    `Max-Age=${maxAgeSeconds}`,
  ];
  if (process.env.NODE_ENV === "production") {
    flags.push("Secure");
  }
  return flags.join("; ");
}

/**
 * Build a ``Set-Cookie`` value that expires the JWT cookie immediately.
 * Used by the logout route.
 */
export function buildJwtClearCookieHeader(): string {
  const flags = [`${COOKIE_NAME}=`, "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=0"];
  if (process.env.NODE_ENV === "production") {
    flags.push("Secure");
  }
  return flags.join("; ");
}
