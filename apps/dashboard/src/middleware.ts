/**
 * Auth middleware (#300, Phase B1) — redirect unauthenticated
 * browser requests to ``/login``, preserving the originally-requested
 * path in the ``?next=`` query so the post-login handler can return
 * the user where they were going.
 *
 * Public paths (no cookie required):
 *
 * - ``/login``, ``/signup``, ``/forgot-password``, ``/reset-password``
 *   — the auth pages themselves. Without this carve-out the redirect
 *   would loop.
 * - ``/api/auth/*`` — login / logout / register / me. These set or
 *   clear the cookie themselves; gating them at the middleware would
 *   make login itself unreachable.
 * - ``/api/auth/oauth/*`` — OAuth callbacks land here from the
 *   provider. They have no cookie *yet*; this is where they get one.
 *
 * The ``config.matcher`` further excludes static assets so the build
 * pipeline doesn't pay the redirect tax for every CSS/font fetch.
 */

import { NextRequest, NextResponse } from "next/server";

import { JWT_COOKIE_NAME } from "@/lib/auth/cookies";

const PUBLIC_PAGES = new Set([
  "/login",
  "/signup",
  "/forgot-password",
  "/reset-password",
]);

/**
 * Explicit allowlist of dashboard-owned auth routes. Every path here
 * has a dedicated route handler under ``src/app/api/auth/...`` that
 * owns the cookie lifecycle. We do **not** want to allow
 * ``/api/auth/*`` wildcard — that would let unknown paths fall
 * through to the catch-all proxy (``/api/[...path]``) and reach the
 * engine's raw auth endpoints (``/auth/jwt/login`` would return the
 * access token to the browser). The catch-all has its own block-list
 * for ``/auth/*`` to make this defence-in-depth (Copilot review on
 * PR #314).
 */
const PUBLIC_AUTH_API_PATHS = new Set([
  "/api/auth/login",
  "/api/auth/logout",
  "/api/auth/register",
  "/api/auth/me",
]);

/**
 * Path prefixes the middleware lets through for the OAuth flow.
 * The provider redirects the user to ``/api/auth/oauth/<provider>/...``
 * which doesn't have a cookie yet — that's the whole point of the
 * dance. Lands when B5 implements the OAuth buttons; until then the
 * prefix is dead-but-harmless.
 */
const PUBLIC_AUTH_API_PREFIXES = ["/api/auth/oauth/"];

function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PAGES.has(pathname)) return true;
  if (PUBLIC_AUTH_API_PATHS.has(pathname)) return true;
  if (PUBLIC_AUTH_API_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return true;
  }
  return false;
}

export function middleware(request: NextRequest): NextResponse {
  const { pathname, search } = request.nextUrl;

  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  const hasJwt = request.cookies.get(JWT_COOKIE_NAME)?.value;
  if (hasJwt) {
    return NextResponse.next();
  }

  // For client-side API calls (``/api/...``) we don't redirect — that
  // would turn a JSON fetch into a 307 with HTML, which the client
  // can't parse. Return 401 JSON instead so the ``fetch`` caller can
  // route the user to /login via the React-Query 401 handler.
  if (pathname.startsWith("/api/")) {
    return new NextResponse(
      JSON.stringify({ detail: "Not authenticated" }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    );
  }

  // Page navigation — redirect to /login with the originating URL.
  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  loginUrl.search = "";
  loginUrl.searchParams.set("next", pathname + search);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  /**
   * Match every path *except* static assets and Next internals.
   *
   * Pattern explainer:
   * - ``/((?!_next/static|_next/image|favicon\\.ico|.*\\.\\w+).*)``
   *   matches "/<anything>" but excludes:
   *   - ``_next/static`` — built JS/CSS bundles
   *   - ``_next/image`` — image optimisation endpoint
   *   - ``favicon.ico`` — browser icon
   *   - ``.\\w+`` — any URL ending in a file extension (``.css``,
   *     ``.png``, ``.svg``...). Cheap heuristic that excludes static
   *     assets served from ``/public``.
   */
  matcher: ["/((?!_next/static|_next/image|favicon\\.ico|.*\\.\\w+).*)"],
};
