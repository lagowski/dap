/**
 * Tests for ``useRunEvents`` — the SSE live-output hook (#662 Phase 3c).
 *
 * jsdom has no native ``EventSource``, so we install a small mock on the
 * global that records constructed instances and lets tests dispatch
 * named SSE events (``node_log``, ``run_finished``, ``snapshot``,
 * ``run_status``). Each mock instance tracks whether ``close()`` was
 * called so the lifecycle contract (open on enabled, close on disable /
 * unmount / run_finished) is verifiable.
 *
 * Pinned contracts:
 *   - enabled=false opens nothing.
 *   - enabled=true opens exactly one EventSource at the proxied URL.
 *   - node_log frames append lines in arrival order.
 *   - run_finished stops streaming and closes the source.
 *   - flipping enabled→false closes the source.
 *   - unmount closes the source.
 *   - the line buffer is capped (oldest lines dropped).
 *   - malformed frames are ignored, not thrown.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { RUN_LOG_LINE_CAP, useRunEvents } from "./use-run-events";

// ---------------------------------------------------------------------------
// EventSource mock
// ---------------------------------------------------------------------------

type Listener = (ev: MessageEvent) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  readyState = 0;
  closed = false;
  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string) {
    this.url = url;
    this.readyState = 1;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: Listener): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }

  removeEventListener(type: string, fn: Listener): void {
    this.listeners.get(type)?.delete(fn);
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }

  /** Test helper: deliver a named SSE event with a JSON ``data`` payload. */
  emit(type: string, data: unknown): void {
    const json = typeof data === "string" ? data : JSON.stringify(data);
    const ev = { data: json } as MessageEvent;
    this.listeners.get(type)?.forEach((fn) => fn(ev));
  }

  static reset(): void {
    MockEventSource.instances = [];
  }

  static get last(): MockEventSource | undefined {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }
}

beforeEach(() => {
  MockEventSource.reset();
  vi.stubGlobal("EventSource", MockEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useRunEvents", () => {
  it("opens nothing when enabled is false", () => {
    const { result } = renderHook(() =>
      useRunEvents("run-1", { enabled: false }),
    );
    expect(MockEventSource.instances).toHaveLength(0);
    expect(result.current.logLines).toEqual([]);
    expect(result.current.isStreaming).toBe(false);
  });

  it("opens one EventSource at the proxied events URL when enabled", () => {
    renderHook(() => useRunEvents("run-1", { enabled: true }));
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.last!.url).toBe("/api/runs/run-1/events");
  });

  it("appends node_log lines in arrival order", () => {
    const { result } = renderHook(() =>
      useRunEvents("run-1", { enabled: true }),
    );
    act(() => {
      MockEventSource.last!.emit("node_log", {
        run_id: "run-1",
        node_id: "coder",
        seq: 1,
        content: "first\n",
        stream: "stdout",
      });
      MockEventSource.last!.emit("node_log", {
        run_id: "run-1",
        node_id: "coder",
        seq: 2,
        content: "second\n",
        stream: "stdout",
      });
    });
    expect(result.current.logLines.map((l) => l.content)).toEqual([
      "first\n",
      "second\n",
    ]);
    expect(result.current.logLines[0].node_id).toBe("coder");
    expect(result.current.isStreaming).toBe(true);
  });

  it("marks streaming true on snapshot of a running run and exposes current_node", () => {
    const { result } = renderHook(() =>
      useRunEvents("run-1", { enabled: true }),
    );
    act(() => {
      MockEventSource.last!.emit("snapshot", {
        run_id: "run-1",
        final_status: "running",
        current_node: "coder",
        node_statuses: {},
        ended_at: null,
      });
    });
    expect(result.current.currentNode).toBe("coder");
    expect(result.current.isStreaming).toBe(true);
  });

  it("stops streaming and closes the source on run_finished", () => {
    const { result } = renderHook(() =>
      useRunEvents("run-1", { enabled: true }),
    );
    const source = MockEventSource.last!;
    act(() => {
      source.emit("run_finished", {
        run_id: "run-1",
        final_status: "success",
        ended_at: "2026-06-06T00:00:00Z",
      });
    });
    expect(result.current.isStreaming).toBe(false);
    expect(source.closed).toBe(true);
  });

  it("closes the source when enabled flips to false", () => {
    const { rerender } = renderHook(
      ({ enabled }) => useRunEvents("run-1", { enabled }),
      { initialProps: { enabled: true } },
    );
    const source = MockEventSource.last!;
    expect(source.closed).toBe(false);
    rerender({ enabled: false });
    expect(source.closed).toBe(true);
  });

  it("closes the source on unmount", () => {
    const { unmount } = renderHook(() =>
      useRunEvents("run-1", { enabled: true }),
    );
    const source = MockEventSource.last!;
    unmount();
    expect(source.closed).toBe(true);
  });

  it("caps the line buffer, dropping the oldest lines", () => {
    const { result } = renderHook(() =>
      useRunEvents("run-1", { enabled: true }),
    );
    const overflow = RUN_LOG_LINE_CAP + 50;
    act(() => {
      for (let i = 0; i < overflow; i++) {
        MockEventSource.last!.emit("node_log", {
          run_id: "run-1",
          node_id: "coder",
          seq: i,
          content: `line-${i}`,
          stream: "stdout",
        });
      }
    });
    expect(result.current.logLines).toHaveLength(RUN_LOG_LINE_CAP);
    // Oldest dropped: the buffer ends at the newest line and starts after
    // the dropped prefix.
    expect(result.current.logLines[result.current.logLines.length - 1].content).toBe(
      `line-${overflow - 1}`,
    );
    expect(result.current.logLines[0].content).toBe(
      `line-${overflow - RUN_LOG_LINE_CAP}`,
    );
  });

  it("ignores malformed JSON frames without throwing", () => {
    const { result } = renderHook(() =>
      useRunEvents("run-1", { enabled: true }),
    );
    act(() => {
      MockEventSource.last!.emit("node_log", "{not json");
      MockEventSource.last!.emit("node_log", {
        run_id: "run-1",
        node_id: "coder",
        seq: 1,
        content: "valid\n",
        stream: "stdout",
      });
    });
    expect(result.current.logLines.map((l) => l.content)).toEqual(["valid\n"]);
  });
});
