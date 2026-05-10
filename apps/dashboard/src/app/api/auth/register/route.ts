/**
 * POST /api/auth/register — proxy fastapi-users signup, then auto-login.
 *
 * The engine's ``/auth/register`` returns the new ``User`` row (without
 * a token). To keep the dashboard form flow simple ("type email,
 * password, click submit, you're in"), we follow the register call
 * with an immediate login so the user lands authenticated.
 *
 * Self-registration is disabled in admin-bootstrap deployments by the
 * engine itself — this route doesn't try to second-guess that policy,
 * it just surfaces whatever the engine returns.
 */

import { NextResponse } from "next/server";

import { buildJwtCookieHeader } from "@/lib/auth/cookies";
import { getEngineUrl } from "@/lib/auth/engine";

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

  const engineRegister = await fetch(`${getEngineUrl()}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: payload.email, password: payload.password }),
  });

  if (!engineRegister.ok) {
    let detail: unknown;
    try {
      detail = await engineRegister.json();
    } catch {
      detail = await engineRegister.text();
    }
    return NextResponse.json(
      typeof detail === "object" && detail !== null ? detail : { detail },
      { status: engineRegister.status },
    );
  }

  // Register succeeded — auto-login so the cookie lands.
  const form = new URLSearchParams();
  form.set("username", payload.email);
  form.set("password", payload.password);
  const engineLogin = await fetch(`${getEngineUrl()}/auth/jwt/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  if (!engineLogin.ok) {
    // The register succeeded but the login didn't — unusual (e.g. the
    // engine has email verification on and the new account is in a
    // pending state). Return 201 without a cookie so the form can
    // show an informational message.
    return NextResponse.json(
      { ok: true, verified: false },
      { status: 201 },
    );
  }
  const tokenPayload = (await engineLogin.json()) as { access_token?: unknown };
  if (typeof tokenPayload.access_token !== "string") {
    return NextResponse.json(
      { detail: "Engine returned malformed token payload" },
      { status: 502 },
    );
  }

  const response = NextResponse.json({ ok: true, verified: true }, { status: 201 });
  response.headers.set(
    "Set-Cookie",
    buildJwtCookieHeader(tokenPayload.access_token, DEFAULT_TOKEN_TTL_SECONDS),
  );
  return response;
}
