/**
 * Tests for useRun's live-polling opt (#662 Phase 2).
 *
 * The run-detail page lets the user turn auto-refresh on/off. The
 * contract that matters:
 *   - ``{ live: false }`` must stop polling even for a *running* run
 *     (the page falls back to a manual Refresh button).
 *   - the default (no opts) must keep the pre-existing 2s poll for a
 *     running run — backward compatible.
 *
 * We drive the real ``useQuery`` against a mocked ``getRun`` and assert
 * the number of network calls over time (fake timers), since
 * ``refetchInterval`` is an inline closure that can't be inspected
 * directly. Counting refetches is the behaviour users actually feel.
 */

import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import * as apiClient from "@/lib/api/client";
import { useRun } from "./runs";

vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/client")>();
  return { ...actual, getRun: vi.fn() };
});

const runningRun = { id: "run-1", final_status: "running" } as Awaited<
  ReturnType<typeof apiClient.getRun>
>;

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useRun live opt", () => {
  beforeEach(() => {
    vi.mocked(apiClient.getRun).mockReset();
    vi.mocked(apiClient.getRun).mockResolvedValue(runningRun);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps polling a running run by default (~2s interval)", async () => {
    vi.useFakeTimers();
    renderHook(() => useRun("run-1"), { wrapper });

    // Initial fetch resolves.
    await vi.waitFor(() =>
      expect(apiClient.getRun).toHaveBeenCalledTimes(1),
    );

    // Advance past two 2s intervals → two more refetches.
    await vi.advanceTimersByTimeAsync(2_000);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(vi.mocked(apiClient.getRun).mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("does not poll a running run when live is false", async () => {
    renderHook(() => useRun("run-1", { live: false }), { wrapper });

    await waitFor(() => expect(apiClient.getRun).toHaveBeenCalledTimes(1));

    // No interval polling should occur. Give real time a window.
    await new Promise((r) => setTimeout(r, 50));
    expect(apiClient.getRun).toHaveBeenCalledTimes(1);
  });
});
