/**
 * GET /api/auth/oauth/callback — final hop of the OAuth dance.
 *
 * The engine's OAuth callback (``GET /auth/<provider>/callback``)
 * receives the provider's auth code, exchanges it for a JWT, and —
 * because ``DAP_AUTH_OAUTH_REDIRECT_URL`` points at this URL —
 * redirects the browser here with the token as ``?token=<jwt>``.
 *
 * We promote the token to an httpOnly ``dap-jwt`` cookie (same
 * shape password login produces) and bounce the user to the home
 * page. Token never appears in the dashboard's URL bar after the
 * redirect (we replace it with a clean ``/`` Location header).
 *
 * If the engine couldn't authenticate the user, ``?error=...`` lands
 * here instead; we forward that to ``/login?oauth_error=<message>``
 * so the page can surface it.
 */

import { NextResponse } from "next/server";

import { buildJwtCookieHeader, getJwtCookieMaxAge } from "@/lib/auth/cookies";

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const token = url.searchParams.get("token");
  const error = url.searchParams.get("error");
  const detail = url.searchParams.get("error_description");

  // The engine always uses ``token`` for the success path. Errors
  // arrive as ``error`` (and sometimes ``error_description``) — same
  // pattern as the OAuth provider's own error redirect.
  if (!token) {
    const message =
      error ?? detail ?? "OAuth flow returned no token. Please try again.";
    const loginUrl = new URL("/login", url);
    loginUrl.searchParams.set("oauth_error", message);
    return NextResponse.redirect(loginUrl, 303);
  }

  const homeUrl = new URL("/", url);
  const response = NextResponse.redirect(homeUrl, 303);
  response.headers.set(
    "Set-Cookie",
    buildJwtCookieHeader(token, getJwtCookieMaxAge()),
  );
  return response;
}
