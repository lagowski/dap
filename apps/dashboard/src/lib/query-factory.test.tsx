/**
 * Tests for the React Query hook factories (#778 Phase 3).
 *
 * `createEntityQuery` / `createInvalidatingMutation` / `refetchWhile`
 * consolidate the "if id then query else noop", "mutate → invalidate
 * sibling keys" and "poll while busy" patterns repeated across
 * `hooks/api.ts` and `hooks/api/runs.ts`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  createEntityQuery,
  createInvalidatingMutation,
  refetchWhile,
} from "./query-factory";

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

describe("createEntityQuery", () => {
  it("fetches when an id is provided", async () => {
    const qc = makeClient();
    const queryFn = vi.fn(async (id: string) => ({ id, name: "Widget" }));
    const useWidget = createEntityQuery({
      scope: "widgets",
      queryKey: (id) => ["widgets", id],
      queryFn,
    });

    const { result } = renderHook(() => useWidget("w1"), {
      wrapper: makeWrapper(qc),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ id: "w1", name: "Widget" });
    expect(queryFn).toHaveBeenCalledWith("w1");
  });

  it("stays idle (no fetch) when id is null", async () => {
    const qc = makeClient();
    const queryFn = vi.fn(async (id: string) => ({ id }));
    const useWidget = createEntityQuery({
      scope: "widgets",
      queryKey: (id) => ["widgets", id],
      queryFn,
    });

    const { result } = renderHook(() => useWidget(null), {
      wrapper: makeWrapper(qc),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(queryFn).not.toHaveBeenCalled();
  });

  it("respects options.enabled = false even with an id", () => {
    const qc = makeClient();
    const queryFn = vi.fn(async (id: string) => ({ id }));
    const useWidget = createEntityQuery({
      scope: "widgets",
      queryKey: (id) => ["widgets", id],
      queryFn,
    });

    const { result } = renderHook(() => useWidget("w1", { enabled: false }), {
      wrapper: makeWrapper(qc),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(queryFn).not.toHaveBeenCalled();
  });

  it("uses a per-suffix noop key so two disabled hooks don't collide", () => {
    const qc = makeClient();
    const useWidget = createEntityQuery({
      scope: "widgets",
      queryKey: (id) => ["widgets", id],
      queryFn: async (id: string) => ({ id }),
    });
    const useWidgetUsage = createEntityQuery({
      scope: "widgets",
      suffix: ["usage"],
      queryKey: (id) => ["widgets", id, "usage"],
      queryFn: async () => ({ count: 1 }),
    });

    const wrapper = makeWrapper(qc);
    renderHook(() => useWidget(null), { wrapper });
    renderHook(() => useWidgetUsage(null), { wrapper });

    const keys = qc
      .getQueryCache()
      .getAll()
      .map((q) => q.queryKey);
    expect(keys).toContainEqual(["widgets", "noop"]);
    expect(keys).toContainEqual(["widgets", "noop", "usage"]);
  });
});

describe("createInvalidatingMutation", () => {
  it("runs the mutation and invalidates the derived keys", async () => {
    const qc = makeClient();
    qc.setQueryData(["widgets"], [{ id: "w1" }]);
    qc.setQueryData(["widgets", "w1"], { id: "w1" });
    qc.setQueryData(["unrelated"], { ok: true });

    const mutationFn = vi.fn(async (vars: { id: string }) => ({ ...vars }));
    const useRenameWidget = createInvalidatingMutation({
      mutationFn,
      invalidates: (vars: { id: string }) => [["widgets"], ["widgets", vars.id]],
    });

    const { result } = renderHook(() => useRenameWidget(), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate({ id: "w1" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mutationFn).toHaveBeenCalledWith({ id: "w1" });
    const cache = qc.getQueryCache();
    expect(cache.find({ queryKey: ["widgets"] })?.state.isInvalidated).toBe(true);
    expect(cache.find({ queryKey: ["widgets", "w1"] })?.state.isInvalidated).toBe(
      true,
    );
    expect(cache.find({ queryKey: ["unrelated"] })?.state.isInvalidated).toBe(
      false,
    );
  });
});

describe("refetchWhile", () => {
  type Probe = { busy: boolean };
  const interval = refetchWhile<Probe>((d) => d.busy, 2_000);

  function asQuery(data: Probe | undefined) {
    return { state: { data } };
  }

  it("keeps polling while the predicate says busy", () => {
    expect(interval(asQuery({ busy: true }))).toBe(2_000);
  });

  it("stops polling once the data is settled", () => {
    expect(interval(asQuery({ busy: false }))).toBe(false);
  });

  it("polls while data is still undefined (first fetch in flight)", () => {
    expect(interval(asQuery(undefined))).toBe(2_000);
  });
});
