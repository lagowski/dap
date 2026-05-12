/**
 * POST /api/auth/reset-password — proxy fastapi-users reset-password.
 *
 * Body: ``{token, password}``. Engine returns 200 on success, 400
 * when the token is invalid / expired / used, or the password
 * fails the engine's ``validate_password`` rule (currently length
 * >= 8).
 *
 * No cookie side-effects: a successful reset doesn't auto-login.
 * The reset-password page redirects the user to ``/login`` so
 * they sign in with the new credentials explicitly — keeps the
 * audit trail clean.
 */

import { NextResponse } from "next/server";

import { getEngineUrl } from "@/lib/auth/engine";

export async function POST(request: Request): Promise<NextResponse> {
  let payload: { token?: unknown; password?: unknown };
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }
  if (typeof payload.token !== "string" || typeof payload.password !== "string") {
    return NextResponse.json(
      { detail: "token + password required" },
      { status: 400 },
    );
  }

  const engineResponse = await fetch(`${getEngineUrl()}/auth/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: payload.token, password: payload.password }),
  });

  if (!engineResponse.ok) {
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

  return NextResponse.json({ ok: true });
}
