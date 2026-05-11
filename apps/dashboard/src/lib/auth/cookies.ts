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
 * Short-lived cookie that proves the current browser initiated an
 * OAuth flow. Set when the user clicks ``Continue with GitHub`` /
 * ``…Google``, required when the OAuth callback lands. Without it
 * an attacker could trick a victim into visiting
 * ``/api/auth/oauth/callback?token=<attacker_jwt>`` and log them
 * into the attacker's account (login-CSRF / session fixation).
 * See ``api/auth/oauth/[provider]/route.ts``.
 */
export const OAUTH_FLOW_COOKIE_NAME = "dap-oauth-flow";

/**
 * Build a ``Set-Cookie`` value for the OAuth-flow nonce. 10 minutes is
 * enough for the slowest interactive auth (typing a 2FA code, picking
 * an account, granting consent) without leaving the cookie around if
 * the user abandons the flow.
 */
export function buildOAuthFlowCookieHeader(value: string): string {
  const flags = [
    `${OAUTH_FLOW_COOKIE_NAME}=${value}`,
    "HttpOnly",
    "SameSite=Lax",
    "Path=/",
    "Max-Age=600",
  ];
  if (process.env.NODE_ENV === "production") {
    flags.push("Secure");
  }
  return flags.join("; ");
}

/** Set-Cookie value that expires the OAuth-flow cookie immediately. */
export function buildOAuthFlowClearCookieHeader(): string {
  const flags = [
    `${OAUTH_FLOW_COOKIE_NAME}=`,
    "HttpOnly",
    "SameSite=Lax",
    "Path=/",
    "Max-Age=0",
  ];
  if (process.env.NODE_ENV === "production") {
    flags.push("Secure");
  }
  return flags.join("; ");
}

/**
 * Cookie ``Max-Age`` for the JWT — must stay in lockstep with the
 * engine's access-token TTL or the dashboard will keep presenting an
 * apparently-authenticated cookie that the engine rejects (or expire
 * users early). Single source of truth: the
 * ``DAP_AUTH_ACCESS_TTL_SECONDS`` server-side env var, which both
 * the engine config (``EngineConfig.auth_access_ttl_seconds``) and
 * this dashboard read. Falls back to the engine's documented
 * default (3600s = 1h) when unset.
 *
 * Centralising the lookup here keeps the login / register / future
 * refresh flows consistent — they all import this value instead of
 * hard-coding their own.
 */
export function getJwtCookieMaxAge(): number {
  const raw = process.env.DAP_AUTH_ACCESS_TTL_SECONDS;
  if (raw === undefined || raw === "") return 3600;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 3600;
}

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
