/**
 * Tests for useLiveUpdates (#662 Phase 2).
 *
 * Pins the three contracts the run-detail page relies on:
 *   1. Defaults to ``true`` when nothing is persisted — preserves the
 *      pre-existing auto-poll behaviour for first-time users.
 *   2. Writes the choice to localStorage on change so it survives reloads.
 *   3. Reads the persisted value on mount (``false`` stays off).
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, render } from "@testing-library/react";

import { useLiveUpdates } from "./use-live-updates";

const STORAGE_KEY = "dap:run-live-updates";

// Tiny harness that surfaces the hook's tuple to the test via refs.
function Harness({ onReady }: { onReady: (api: ReturnType<typeof useLiveUpdates>) => void }) {
  const api = useLiveUpdates();
  onReady(api);
  return <span data-testid="live">{String(api[0])}</span>;
}

describe("useLiveUpdates", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
  });

  it("defaults to true when nothing is persisted", () => {
    let live: boolean | undefined;
    render(<Harness onReady={([v]) => (live = v)} />);
    expect(live).toBe(true);
  });

  it("persists the choice to localStorage on change", () => {
    let setLive: ((v: boolean) => void) | undefined;
    render(<Harness onReady={([, s]) => (setLive = s)} />);

    act(() => setLive!(false));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("false");

    act(() => setLive!(true));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("true");
  });

  it("reads the persisted value on mount", () => {
    window.localStorage.setItem(STORAGE_KEY, "false");
    let live: boolean | undefined;
    render(<Harness onReady={([v]) => (live = v)} />);
    expect(live).toBe(false);
  });
});
