/**
 * GET /api/auth/oauth/<provider> — kick off an OAuth flow.
 *
 * fastapi-users' authorize endpoint (``GET /auth/<provider>/authorize``)
 * returns a JSON body with the provider's authorization URL. The
 * browser can't use that directly — we fetch it server-side and
 * redirect the user to the real authorization URL with a 307 so the
 * provider sees a normal navigation.
 *
 * The provider eventually redirects to the engine's
 * ``/auth/<provider>/callback``, which (with the
 * ``DAP_AUTH_OAUTH_REDIRECT_URL`` env var set) bounces back to
 * ``/api/auth/oauth/callback`` with a ``?token=`` query param. See
 * ``../callback/route.ts`` for that side.
 */

import { NextResponse } from "next/server";

import { getEngineUrl } from "@/lib/auth/engine";

const ALLOWED_PROVIDERS = new Set(["github", "google"]);

export async function GET(
  _request: Request,
  ctx: { params: Promise<{ provider: string }> },
): Promise<NextResponse> {
  const { provider } = await ctx.params;
  if (!ALLOWED_PROVIDERS.has(provider)) {
    return NextResponse.json(
      { detail: `Unknown OAuth provider: ${provider}` },
      { status: 404 },
    );
  }

  const engineResponse = await fetch(
    `${getEngineUrl()}/auth/${provider}/authorize`,
  );
  if (!engineResponse.ok) {
    // 404 means the provider router isn't mounted — operator forgot
    // the OAuth client_id / client_secret env vars. Surface as 503
    // so the dashboard form can show "OAuth not configured".
    let detail: unknown;
    try {
      detail = await engineResponse.json();
    } catch {
      detail = await engineResponse.text();
    }
    return NextResponse.json(
      typeof detail === "object" && detail !== null
        ? detail
        : { detail: String(detail) },
      { status: engineResponse.status === 404 ? 503 : engineResponse.status },
    );
  }

  const body = (await engineResponse.json()) as { authorization_url?: unknown };
  if (typeof body.authorization_url !== "string") {
    return NextResponse.json(
      { detail: "Engine returned malformed authorize payload" },
      { status: 502 },
    );
  }
  return NextResponse.redirect(body.authorization_url, 307);
}
