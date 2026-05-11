/**
 * GET /api/auth/oauth/callback — final hop of the OAuth dance.
 *
 * The engine's OAuth callback (``GET /auth/<provider>/callback``)
 * receives the provider's auth code, exchanges it for a JWT, and —
 * because ``DAP_AUTH_OAUTH_REDIRECT_URL`` points at this URL —
 * redirects the browser here with the token as ``?token=<jwt>``.
 *
 * Three guards in order (Copilot review on PR #326):
 *
 * 1. **Flow-nonce cookie** — required. Set when the user clicked
 *    "Continue with <provider>" on /login or /signup. Without it
 *    a crafted ``/api/auth/oauth/callback?token=...`` link couldn't
 *    log a victim into an attacker's account (the victim's browser
 *    never set the cookie).
 * 2. **Engine error pass-through** — if ``?error=`` arrives instead
 *    of a token, surface it on /login.
 * 3. **Success path** — promote the token to an httpOnly cookie,
 *    redirect to ``/``. The token never appears in the dashboard
 *    URL bar after the redirect.
 *
 * Response hardening:
 * - ``Cache-Control: no-store`` so intermediate caches can't pin a
 *   redirect carrying credentials.
 * - ``Referrer-Policy: no-referrer`` so the followup navigation
 *   to ``/`` doesn't send the token-bearing URL in ``Referer``.
 */

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  OAUTH_FLOW_COOKIE_NAME,
  buildJwtCookieHeader,
  buildOAuthFlowClearCookieHeader,
  getJwtCookieMaxAge,
} from "@/lib/auth/cookies";

function harden(response: NextResponse): NextResponse {
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

function loginErrorRedirect(origin: string, message: string): NextResponse {
  const url = new URL("/login", origin);
  url.searchParams.set("oauth_error", message);
  const response = NextResponse.redirect(url, 303);
  // Clear the flow cookie on the error path too — the flow is over,
  // successful or not, and a stale nonce should not survive.
  response.headers.append("Set-Cookie", buildOAuthFlowClearCookieHeader());
  return harden(response);
}

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const token = url.searchParams.get("token");
  const error = url.searchParams.get("error");
  const detail = url.searchParams.get("error_description");

  // Guard 1 — require proof this browser started the flow.
  const flowCookie = (await cookies()).get(OAUTH_FLOW_COOKIE_NAME)?.value;
  if (!flowCookie) {
    return loginErrorRedirect(
      request.url,
      "OAuth callback received without an in-flight flow. Start sign-in again.",
    );
  }

  // Guard 2 — engine errors surface back to the user without a cookie.
  if (!token) {
    const message =
      error ?? detail ?? "OAuth flow returned no token. Please try again.";
    return loginErrorRedirect(request.url, message);
  }

  // Guard 3 — success path: mint the JWT cookie + clear the flow
  // nonce in the same response.
  const homeUrl = new URL("/", request.url);
  const response = NextResponse.redirect(homeUrl, 303);
  response.headers.append(
    "Set-Cookie",
    buildJwtCookieHeader(token, getJwtCookieMaxAge()),
  );
  response.headers.append("Set-Cookie", buildOAuthFlowClearCookieHeader());
  return harden(response);
}
