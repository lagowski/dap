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
const HOP_HEADERS = new Set([
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
]);

async function proxy(
  request: Request,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await ctx.params;
  const pathname = "/" + path.map(encodeURIComponent).join("/");
  const search = new URL(request.url).search;
  const target = `${getEngineUrl()}${pathname}${search}`;

  const headers = new Headers();
  for (const [key, value] of request.headers.entries()) {
    if (!HOP_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  }

  const jwt = (await cookies()).get(JWT_COOKIE_NAME)?.value;
  if (jwt) {
    headers.set("Authorization", `Bearer ${jwt}`);
  } else {
    // Don't leak a stale Authorization header from the inbound
    // request if there's no cookie — every browser-side caller
    // must rely on the cookie.
    headers.delete("Authorization");
  }

  // Body is only present for non-GET/HEAD requests. ``duplex: 'half'``
  // is required by Node 18+ when ``body`` is a ReadableStream.
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  const engineResponse = await fetch(target, init);

  // Stream back — preserve status code, body, and engine headers
  // except hop-by-hop ones. Content-encoding is the engine's
  // choice; pass through.
  const responseHeaders = new Headers();
  for (const [key, value] of engineResponse.headers.entries()) {
    if (!HOP_HEADERS.has(key.toLowerCase())) {
      responseHeaders.set(key, value);
    }
  }
  const buffer = await engineResponse.arrayBuffer();
  return new NextResponse(buffer, {
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
