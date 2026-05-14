/**
 * GET /api/auth/me — return the current user, derived from the JWT
 * cookie via the engine's ``/users/me``.
 *
 * Used by ``useCurrentUser`` to drive UI state (header chrome,
 * conditional admin links) and by middleware fallbacks. Returns
 * 401 with an empty body when the cookie is missing or invalid —
 * the React hook treats that as "logged out" and shows the
 * sign-in entry point.
 */

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { JWT_COOKIE_NAME } from "@/lib/auth/cookies";
import { getEngineUrl } from "@/lib/auth/engine";

export async function GET(): Promise<NextResponse> {
  const jwt = (await cookies()).get(JWT_COOKIE_NAME)?.value;
  if (!jwt) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const engineResponse = await fetch(`${getEngineUrl()}/users/me`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });

  if (!engineResponse.ok) {
    // Cookie was stale or revoked. Treat as "logged out" — the
    // client clears its session state via the 401 path.
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

  const body = await engineResponse.json();
  return NextResponse.json(body);
}
