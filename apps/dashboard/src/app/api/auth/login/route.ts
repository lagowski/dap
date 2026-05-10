/**
 * POST /api/auth/login — accept email + password, exchange for JWT
 * with the engine, set the httpOnly cookie.
 *
 * The engine speaks OAuth2 Password Flow (fastapi-users default) so
 * the engine endpoint takes form-encoded ``username`` + ``password``,
 * not JSON. We accept JSON from the browser, translate to form, and
 * return JSON back — that keeps the browser API consistent across all
 * dashboard endpoints.
 */

import { NextResponse } from "next/server";

import { buildJwtCookieHeader } from "@/lib/auth/cookies";
import { getEngineUrl } from "@/lib/auth/engine";

// Engine default access-token TTL is 1 hour (3600s). Keep the cookie
// in lockstep — once the JWT expires the engine will 401 anyway and
// the next request rolls the user to /login via middleware.
const DEFAULT_TOKEN_TTL_SECONDS = 3600;

export async function POST(request: Request): Promise<NextResponse> {
  let payload: { email?: unknown; password?: unknown };
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }

  if (typeof payload.email !== "string" || typeof payload.password !== "string") {
    return NextResponse.json(
      { detail: "email + password required" },
      { status: 400 },
    );
  }

  const form = new URLSearchParams();
  form.set("username", payload.email);
  form.set("password", payload.password);

  const engineResponse = await fetch(`${getEngineUrl()}/auth/jwt/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });

  if (!engineResponse.ok) {
    // Pass the engine's error body through so the form can show the
    // exact fastapi-users error message ("LOGIN_BAD_CREDENTIALS",
    // "LOGIN_USER_NOT_VERIFIED", etc).
    let detail: unknown;
    try {
      detail = await engineResponse.json();
    } catch {
      detail = await engineResponse.text();
    }
    return NextResponse.json(
      typeof detail === "object" && detail !== null ? detail : { detail },
      { status: engineResponse.status },
    );
  }

  const tokenPayload = (await engineResponse.json()) as {
    access_token?: unknown;
    token_type?: unknown;
  };
  if (typeof tokenPayload.access_token !== "string") {
    return NextResponse.json(
      { detail: "Engine returned malformed token payload" },
      { status: 502 },
    );
  }

  const response = NextResponse.json({ ok: true });
  response.headers.set(
    "Set-Cookie",
    buildJwtCookieHeader(tokenPayload.access_token, DEFAULT_TOKEN_TTL_SECONDS),
  );
  return response;
}
