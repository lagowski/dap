/**
 * Catch-all proxy: ``/api/<anything>`` → engine ``<anything>``.
 *
 * Why a proxy?
 *
 * - The browser never sees the JWT (it's an httpOnly cookie).
 * - The dashboard already runs on the same origin the user types in,
 *   so cookies attach automatically — no CORS gymnastics.
 * - Every API call carries the bearer transparently; the route
 *   handler reads it from the cookie and forwards it as
 *   ``Authorization: Bearer <jwt>``.
 *
 * The ``/api/auth/*`` routes (login, logout, register, me) are
 * dedicated handlers because they need cookie-write side-effects.
 * Next.js routes them by *file path*, so the explicit handlers win
 * over this catch-all for those paths.
 */

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { JWT_COOKIE_NAME } from "@/lib/auth/cookies";
import { getEngineUrl } from "@/lib/auth/engine";

// Headers a route handler must NOT forward — they describe the
// hop, not the message. ``host`` would point at the dashboard's
// origin, ``content-length`` would mismatch after our re-encode,
// etc.
//
// ``cookie`` is stripped on the request leg even though it's not
// strictly hop-by-hop: the browser sends ``dap-jwt`` (and any
// other dashboard cookies) on every request, but the *engine*
// authenticates via the ``Authorization: Bearer`` we synthesise
// below. Forwarding ``Cookie`` would leak the JWT into engine
// logs / observability and bypass our cookie-only auth surface.
const REQUEST_HOP_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "content-length",
  "cookie",
]);

// Response headers stripped on the way back. ``set-cookie`` is
// dropped specifically so the engine can't plant cookies on the
// dashboard's origin — only the dedicated ``/api/auth/*`` handlers
// set cookies, and they do so explicitly. Future engine endpoints
// (e.g. one that experiments with server-side sessions) would
// otherwise silently mutate our auth surface.
const RESPONSE_HOP_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "content-length",
  "set-cookie",
]);

/**
 * Paths the catch-all refuses to proxy. Most of ``/auth/*`` is the
 * engine's own session-token surface (``/auth/jwt/login``,
 * ``/auth/register``, OAuth callbacks, …); proxying those would
 * leak the raw access token to the browser and bypass our
 * cookie-only design. The dedicated ``/api/auth/login`` etc.
 * handlers Next routes to file paths *before* this catch-all, so
 * they still work — this guard blocks only fall-through traffic.
 *
 * ``/auth/api-tokens`` is the one safe sub-tree under ``/auth``:
 * - admin GET / DELETE return token metadata or 204 — no secrets
 * - user-scoped POST returns a freshly minted token, which the user
 *   explicitly asked for (that's the whole point of the API token
 *   surface — they need the value once to paste into a CLI / script)
 * Letting it through this proxy lets ``/admin/api-tokens`` (and any
 * future "my tokens" page) work without per-route handlers.
 *
 * ``/users/me`` is intentionally allowed because dashboard pages
 * may legitimately want fresh server-side identity (the
 * ``/api/auth/me`` handler wraps it but doesn't gate it).
 */
function isBlockedPath(pathname: string): boolean {
  // Match exactly /auth/api-tokens or anything underneath it. Plain
  // `startsWith("/auth/api-tokens")` would also unblock a future
  // `/auth/api-tokensv2` or similar sibling that does NOT belong to
  // this sub-tree.
  if (pathname === "/auth/api-tokens" || pathname.startsWith("/auth/api-tokens/")) {
    return false;
  }
  return pathname.startsWith("/auth/");
}

async function proxy(
  request: Request,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await ctx.params;
  const pathname = "/" + path.map(encodeURIComponent).join("/");
  if (isBlockedPath(pathname)) {
    return NextResponse.json(
      {
        detail:
          "Auth routes are not proxied. Use the dashboard's /api/auth/* endpoints.",
      },
      { status: 404 },
    );
  }
  const search = new URL(request.url).search;
  const target = `${getEngineUrl()}${pathname}${search}`;

  const headers = new Headers();
  for (const [key, value] of request.headers.entries()) {
    if (!REQUEST_HOP_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  }
  // Authorization is the only auth surface to the engine — synthesise
  // it from the cookie. Strip any inbound value first so a stale /
  // attacker-controlled header can't slip through.
  headers.delete("Authorization");
  const jwt = (await cookies()).get(JWT_COOKIE_NAME)?.value;
  if (jwt) {
    headers.set("Authorization", `Bearer ${jwt}`);
  }

  // Stream the request body straight through. ``duplex: 'half'`` is
  // required by Node 18+ when ``body`` is a ``ReadableStream`` —
  // signals that the client won't be writing more once it starts
  // reading.
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }

  const engineResponse = await fetch(target, init);

  // True streaming on the way back — pass the engine's response
  // body through as a ``ReadableStream`` so large payloads
  // (exports, run state history, future audit log downloads)
  // don't get fully buffered into memory.
  const responseHeaders = new Headers();
  for (const [key, value] of engineResponse.headers.entries()) {
    if (!RESPONSE_HOP_HEADERS.has(key.toLowerCase())) {
      responseHeaders.set(key, value);
    }
  }
  return new NextResponse(engineResponse.body, {
    status: engineResponse.status,
    headers: responseHeaders,
  });
}

export async function GET(
  request: Request,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  return proxy(request, ctx);
}

export async function POST(
  request: Request,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  return proxy(request, ctx);
}

export async function PUT(
  request: Request,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  return proxy(request, ctx);
}

export async function PATCH(
  request: Request,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  return proxy(request, ctx);
}

export async function DELETE(
  request: Request,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  return proxy(request, ctx);
}
