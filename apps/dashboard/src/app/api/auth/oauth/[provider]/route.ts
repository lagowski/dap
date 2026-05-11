/**
 * GET /api/auth/oauth/<provider> — kick off an OAuth flow.
 *
 * fastapi-users' authorize endpoint (``GET /auth/<provider>/authorize``)
 * returns a JSON body with the provider's authorization URL. The
 * browser can't use that directly — we fetch it server-side and
 * redirect the user to the real authorization URL with a 307 so the
 * provider sees a normal navigation.
 *
 * Sets the ``dap-oauth-flow`` cookie before redirecting. The
 * ``callback`` handler requires that cookie to be present, so a
 * crafted ``/api/auth/oauth/callback?token=<attacker_jwt>`` link
 * sent to a victim who never started a flow gets rejected
 * (login-CSRF / session-fixation defence — Copilot review on PR
 * #326).
 *
 * On engine failure (provider not configured, network blip) we
 * redirect the browser to ``/login?oauth_error=...`` rather than
 * returning JSON — the user got here by clicking a Link, so plain
 * JSON in the address bar would be a UX dead-end.
 */

import { randomBytes } from "node:crypto";

import { NextResponse } from "next/server";

import { buildOAuthFlowCookieHeader } from "@/lib/auth/cookies";
import { getEngineUrl } from "@/lib/auth/engine";

const ALLOWED_PROVIDERS = new Set(["github", "google"]);

function loginErrorRedirect(origin: string, message: string): NextResponse {
  const url = new URL("/login", origin);
  url.searchParams.set("oauth_error", message);
  const response = NextResponse.redirect(url, 303);
  // Defence-in-depth: error redirects don't carry credentials, but
  // keep the same hardening profile as the success redirect.
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

export async function GET(
  request: Request,
  ctx: { params: Promise<{ provider: string }> },
): Promise<NextResponse> {
  const { provider } = await ctx.params;
  if (!ALLOWED_PROVIDERS.has(provider)) {
    return loginErrorRedirect(request.url, `Unknown OAuth provider: ${provider}`);
  }

  let engineResponse: Response;
  try {
    engineResponse = await fetch(`${getEngineUrl()}/auth/${provider}/authorize`);
  } catch (error) {
    return loginErrorRedirect(
      request.url,
      `Engine unreachable: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  if (!engineResponse.ok) {
    // 404 = provider router not mounted (missing client_id/secret).
    // Anything else = engine surfaced an error — pass the wording
    // through so operators can see exactly what the engine said.
    const message =
      engineResponse.status === 404
        ? `OAuth provider "${provider}" is not configured on this engine.`
        : `OAuth provider "${provider}" failed: ${engineResponse.statusText}`;
    return loginErrorRedirect(request.url, message);
  }

  const body = (await engineResponse.json()) as { authorization_url?: unknown };
  if (typeof body.authorization_url !== "string") {
    return loginErrorRedirect(
      request.url,
      "Engine returned a malformed OAuth payload.",
    );
  }

  // Set the flow-nonce cookie before the redirect so it lands on the
  // browser before the user wanders off to the provider. ``randomBytes``
  // is plenty — we don't compare the value, we only check the cookie
  // exists at callback time (proof that *this* browser started the
  // flow).
  const flowNonce = randomBytes(16).toString("base64url");
  const response = NextResponse.redirect(body.authorization_url, 307);
  response.headers.set("Set-Cookie", buildOAuthFlowCookieHeader(flowNonce));
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
