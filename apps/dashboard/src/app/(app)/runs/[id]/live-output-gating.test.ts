/**
 * Gating tests for the live-output panel (#662 Phase 3c).
 *
 * The run-detail page unwraps ``params`` with React 19's ``use(Promise)``
 * and pulls in reactflow, so a full page render isn't worth the Suspense
 * fight (see the note in ``page.test.tsx``). Instead the page exposes the
 * pure ``liveOutputState`` decision and we pin its contract directly:
 *   - panel shows only for running / paused runs.
 *   - the SSE subscription (``enabled``) opens only when running/paused AND
 *     Live is on.
 *   - terminal runs never show the panel and never enable streaming.
 */

import { describe, expect, it } from "vitest";

import { liveOutputState } from "./page";

describe("liveOutputState", () => {
  it("shows + enables for a running run with Live on", () => {
    expect(liveOutputState("running", true)).toEqual({
      show: true,
      enabled: true,
    });
  });

  it("shows but does NOT enable for a running run with Live off", () => {
    expect(liveOutputState("running", false)).toEqual({
      show: true,
      enabled: false,
    });
  });

  it("shows + enables for a paused run with Live on", () => {
    expect(liveOutputState("paused", true)).toEqual({
      show: true,
      enabled: true,
    });
  });

  it("hides + disables for terminal runs regardless of Live", () => {
    for (const status of ["success", "failed", "aborted"] as const) {
      expect(liveOutputState(status, true)).toEqual({
        show: false,
        enabled: false,
      });
      expect(liveOutputState(status, false)).toEqual({
        show: false,
        enabled: false,
      });
    }
  });
});
