/**
 * POST /api/auth/forgot-password — proxy fastapi-users forgot-password.
 *
 * Always returns 202 to the browser regardless of whether the email
 * exists — same anti-enumeration shape the engine returns. We don't
 * touch cookies (a reset is independent of any existing session).
 */

import { NextResponse } from "next/server";

import { getEngineUrl } from "@/lib/auth/engine";

export async function POST(request: Request): Promise<NextResponse> {
  let payload: { email?: unknown };
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }
  if (typeof payload.email !== "string") {
    return NextResponse.json({ detail: "email required" }, { status: 400 });
  }

  const engineResponse = await fetch(`${getEngineUrl()}/auth/forgot-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: payload.email }),
  });

  if (!engineResponse.ok) {
    // Read body once as text, then attempt JSON.parse — calling .json()
    // and .text() on the same Response throws "Body has already been read".
    const raw = await engineResponse.text();
    let detail: unknown = raw;
    try {
      detail = JSON.parse(raw);
    } catch {
      // not JSON — keep raw text
    }
    return NextResponse.json(
      typeof detail === "object" && detail !== null ? detail : { detail },
      { status: engineResponse.status },
    );
  }

  return NextResponse.json({ ok: true }, { status: 202 });
}
