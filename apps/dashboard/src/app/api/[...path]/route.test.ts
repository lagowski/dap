/**
 * Tests for the catch-all proxy route at /api/[...path].
 *
 * Covers:
 *   - isBlockedPath guard (auth routes blocked, api-tokens allowed)
 *   - Auth header injection from cookie
 *   - Hop-header stripping on request and response
 *   - 4xx / 5xx status passthrough
 *   - Streaming body passthrough
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextResponse } from "next/server";

// ---------------------------------------------------------------------------
// Module mocks — must be hoisted above the subject import.
// ---------------------------------------------------------------------------

// Mock next/headers so cookies() is controllable in tests.
const mockGet = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => Promise.resolve({ get: mockGet }),
}));

// Mock the engine URL resolver.
vi.mock("@/lib/auth/engine", () => ({
  getEngineUrl: () => "http://engine.test:7333",
}));

// Mock JWT_COOKIE_NAME to a known value.
vi.mock("@/lib/auth/cookies", () => ({
  JWT_COOKIE_NAME: "dap-jwt",
}));

// ---------------------------------------------------------------------------
// Subject import (after mocks are registered).
// ---------------------------------------------------------------------------

import { GET, POST, PATCH, DELETE } from "./route";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a minimal Request object pointing at a dashboard URL.
 * The path segment after /api is what the catch-all captures.
 */
function makeRequest(
  path: string,
  {
    method = "GET",
    headers = {} as Record<string, string>,
    body = null as BodyInit | null,
  } = {},
): Request {
  return new Request(`http://localhost:3000/api/${path}`, {
    method,
    headers,
    body: body ?? undefined,
    // @ts-expect-error duplex not in lib DOM typings but required for Node 18+
    duplex: body ? "half" : undefined,
  });
}

/**
 * Build a ctx object the way Next.js passes it: params is a Promise.
 */
function makeCtx(segments: string[]): { params: Promise<{ path: string[] }> } {
  return { params: Promise.resolve({ path: segments }) };
}

/**
 * Make fetch return a synthetic engine response.
 */
function stubFetch(
  status: number,
  body: string | ReadableStream = "{}",
  responseHeaders: Record<string, string> = { "content-type": "application/json" },
): void {
  // 204 / 304 must not carry a body per the Fetch spec.
  const noBodyStatuses = new Set([204, 304]);
  vi.spyOn(global, "fetch").mockResolvedValueOnce(
    new Response(noBodyStatuses.has(status) ? null : body, { status, headers: responseHeaders }),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.restoreAllMocks();
  // Default: no JWT cookie.
  mockGet.mockReturnValue(undefined);
});

// ── isBlockedPath guard ─────────────────────────────────────────────────────

describe("isBlockedPath guard", () => {
  it("returns 404 for /auth/jwt/login (engine auth surface)", async () => {
    const req = makeRequest("auth/jwt/login", { method: "POST" });
    const ctx = makeCtx(["auth", "jwt", "login"]);
    const res = await POST(req, ctx);
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.detail).toMatch(/not proxied/i);
  });

  it("returns 404 for /auth/register", async () => {
    const req = makeRequest("auth/register", { method: "POST" });
    const ctx = makeCtx(["auth", "register"]);
    const res = await POST(req, ctx);
    expect(res.status).toBe(404);
  });

  it("allows /auth/api-tokens (safe sub-tree)", async () => {
    stubFetch(200, "[]");
    const req = makeRequest("auth/api-tokens");
    const ctx = makeCtx(["auth", "api-tokens"]);
    const res = await GET(req, ctx);
    expect(res.status).toBe(200);
  });

  it("allows /auth/api-tokens/<id> (sub-resource)", async () => {
    stubFetch(204, "");
    const req = makeRequest("auth/api-tokens/abc123", { method: "DELETE" });
    const ctx = makeCtx(["auth", "api-tokens", "abc123"]);
    const res = await DELETE(req, ctx);
    expect(res.status).toBe(204);
  });

  it("does NOT allow a path that starts with /auth/api-tokens but is a sibling (e.g. /auth/api-tokensv2)", async () => {
    const req = makeRequest("auth/api-tokensv2");
    const ctx = makeCtx(["auth", "api-tokensv2"]);
    const res = await GET(req, ctx);
    expect(res.status).toBe(404);
  });
});

// ── Auth header injection ───────────────────────────────────────────────────

describe("auth header injection", () => {
  it("injects Authorization: Bearer from cookie when JWT present", async () => {
    mockGet.mockReturnValue({ value: "test-jwt-token" });
    stubFetch(200);
    const req = makeRequest("runs/1");
    const ctx = makeCtx(["runs", "1"]);
    await GET(req, ctx);

    const [, init] = vi.mocked(global.fetch).mock.calls[0];
    const headers = init?.headers as Headers;
    expect(headers.get("authorization")).toBe("Bearer test-jwt-token");
  });

  it("strips any inbound Authorization header when no cookie is set", async () => {
    mockGet.mockReturnValue(undefined);
    stubFetch(200);
    const req = makeRequest("runs/1", {
      headers: { authorization: "Bearer attacker-token" },
    });
    const ctx = makeCtx(["runs", "1"]);
    await GET(req, ctx);

    const [, init] = vi.mocked(global.fetch).mock.calls[0];
    const headers = init?.headers as Headers;
    expect(headers.get("authorization")).toBeNull();
  });

  it("overwrites an inbound Authorization header with the cookie value", async () => {
    mockGet.mockReturnValue({ value: "real-jwt" });
    stubFetch(200);
    const req = makeRequest("runs/1", {
      headers: { authorization: "Bearer stale-token" },
    });
    const ctx = makeCtx(["runs", "1"]);
    await GET(req, ctx);

    const [, init] = vi.mocked(global.fetch).mock.calls[0];
    const headers = init?.headers as Headers;
    expect(headers.get("authorization")).toBe("Bearer real-jwt");
  });
});

// ── Hop-header stripping ────────────────────────────────────────────────────

describe("hop-header stripping", () => {
  it("strips hop-by-hop headers from the request", async () => {
    mockGet.mockReturnValue(undefined);
    stubFetch(200);
    const req = makeRequest("health", {
      headers: {
        connection: "keep-alive",
        "transfer-encoding": "chunked",
        "content-length": "0",
        cookie: "dap-jwt=leaked",
        "x-custom": "keep-me",
      },
    });
    const ctx = makeCtx(["health"]);
    await GET(req, ctx);

    const [, init] = vi.mocked(global.fetch).mock.calls[0];
    const headers = init?.headers as Headers;
    expect(headers.get("connection")).toBeNull();
    expect(headers.get("transfer-encoding")).toBeNull();
    expect(headers.get("content-length")).toBeNull();
    expect(headers.get("cookie")).toBeNull();
    expect(headers.get("x-custom")).toBe("keep-me");
  });

  it("strips set-cookie from the engine response", async () => {
    mockGet.mockReturnValue(undefined);
    stubFetch(200, "{}", {
      "content-type": "application/json",
      "set-cookie": "engine-session=secret; HttpOnly",
      "x-request-id": "abc",
    });
    const req = makeRequest("runs");
    const ctx = makeCtx(["runs"]);
    const res = await GET(req, ctx);

    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("x-request-id")).toBe("abc");
  });

  it("strips transfer-encoding from the engine response", async () => {
    mockGet.mockReturnValue(undefined);
    stubFetch(200, "{}", {
      "content-type": "application/json",
      "transfer-encoding": "chunked",
    });
    const req = makeRequest("runs");
    const ctx = makeCtx(["runs"]);
    const res = await GET(req, ctx);

    expect(res.headers.get("transfer-encoding")).toBeNull();
  });
});

// ── 4xx / 5xx passthrough ───────────────────────────────────────────────────

describe("4xx / 5xx status passthrough", () => {
  it.each([400, 401, 403, 404, 422, 500, 502, 503])(
    "passes through HTTP %i status unchanged",
    async (status) => {
      mockGet.mockReturnValue(undefined);
      stubFetch(status, JSON.stringify({ detail: "error" }));
      const req = makeRequest("runs/999");
      const ctx = makeCtx(["runs", "999"]);
      const res = await GET(req, ctx);
      expect(res.status).toBe(status);
    },
  );
});

// ── Streaming body passthrough ──────────────────────────────────────────────

describe("streaming body passthrough", () => {
  it("returns a NextResponse whose body is the engine ReadableStream", async () => {
    mockGet.mockReturnValue(undefined);
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode("chunk1"));
        controller.enqueue(encoder.encode("chunk2"));
        controller.close();
      },
    });
    stubFetch(200, stream, { "content-type": "text/event-stream" });

    const req = makeRequest("runs/1/stream");
    const ctx = makeCtx(["runs", "1", "stream"]);
    const res = await GET(req, ctx);

    expect(res).toBeInstanceOf(NextResponse);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/event-stream");

    // Consume the stream to verify it is intact.
    const text = await res.text();
    expect(text).toBe("chunk1chunk2");
  });

  it("forwards request body for POST requests", async () => {
    mockGet.mockReturnValue({ value: "tok" });
    stubFetch(201, JSON.stringify({ id: "r1" }));

    const req = makeRequest("runs", {
      method: "POST",
      body: JSON.stringify({ pipeline_id: "p1" }),
      headers: { "content-type": "application/json" },
    });
    const ctx = makeCtx(["runs"]);
    const res = await POST(req, ctx);
    expect(res.status).toBe(201);

    // Confirm fetch was called with the right method.
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("http://engine.test:7333/runs");
    expect(init?.method).toBe("POST");
  });

  it("proxies PATCH correctly", async () => {
    mockGet.mockReturnValue({ value: "tok" });
    stubFetch(200);
    const req = makeRequest("runs/1", {
      method: "PATCH",
      body: JSON.stringify({ status: "cancelled" }),
      headers: { "content-type": "application/json" },
    });
    const ctx = makeCtx(["runs", "1"]);
    const res = await PATCH(req, ctx);
    expect(res.status).toBe(200);
    const [, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(init?.method).toBe("PATCH");
  });
});

// ── Target URL construction ─────────────────────────────────────────────────

describe("target URL construction", () => {
  it("forwards query string to the engine", async () => {
    mockGet.mockReturnValue(undefined);
    stubFetch(200, "[]");
    const req = new Request("http://localhost:3000/api/runs?limit=5&offset=10");
    const ctx = makeCtx(["runs"]);
    await GET(req, ctx);

    const [url] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("http://engine.test:7333/runs?limit=5&offset=10");
  });
});
