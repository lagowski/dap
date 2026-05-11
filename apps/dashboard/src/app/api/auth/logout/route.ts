/**
 * POST /api/auth/logout — clear the JWT cookie.
 *
 * The engine's ``/auth/jwt/logout`` is a no-op for stateless JWTs
 * (the token stays valid until its natural expiry), so we just drop
 * the cookie on our side. When the engine starts maintaining a
 * refresh-token blacklist (Phase B follow-up), this handler will
 * also POST there so the refresh token gets invalidated server-side.
 */

import { NextResponse } from "next/server";

import { buildJwtClearCookieHeader } from "@/lib/auth/cookies";

export async function POST(): Promise<NextResponse> {
  const response = NextResponse.json({ ok: true });
  response.headers.set("Set-Cookie", buildJwtClearCookieHeader());
  return response;
}
