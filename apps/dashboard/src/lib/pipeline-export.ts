/**
 * Browser-side pipeline export helper (#124).
 *
 * Mirrors ``agent-export.ts``: wraps ``GET /pipelines/{id}/export`` +
 * a Blob download into a single call so the Designer toolbar and any
 * future surface (e.g. a list-page row action) share one
 * implementation. Filename slug uses the pipeline name; falls back
 * to a short id prefix if the slug ends up empty.
 */

import { exportPipeline, formatApiError } from "./api/client";
import type { Pipeline } from "./api/types";

const ID_FALLBACK_PREFIX = 8;

function slugFromPipeline(pipeline: Pick<Pipeline, "id" | "name">): string {
  // ``^-+|-+$`` strips *all* leading/trailing hyphens — the previous
  // ``(^-|-$)+`` only stripped one from each side, so a name that
  // collapses to "----" would become "-" and produce "-.pipeline.json"
  // instead of falling through to the id-prefix fallback.
  return (
    pipeline.name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || pipeline.id.slice(0, ID_FALLBACK_PREFIX)
  );
}

/**
 * Fetch the pipeline's export payload and trigger a browser download.
 *
 * ``bundle: true`` (#126) asks the engine to also include every
 * referenced agent in the file, so the receiving installation can
 * import the whole thing without pre-creating the agents. Filename
 * suffix changes (``.pipeline-bundle.json``) so the user can tell
 * the two file shapes apart at a glance.
 *
 * Throws when ``exportPipeline`` fails — caller decides how to
 * surface (alert / toast / inline message).
 */
export async function downloadPipelineExport(
  pipeline: Pipeline,
  options: { bundle?: boolean } = {},
): Promise<void> {
  const payload = await exportPipeline(pipeline.id, { bundle: options.bundle });
  const suffix = options.bundle ? ".pipeline-bundle.json" : ".pipeline.json";
  saveJson(payload, `${slugFromPipeline(pipeline)}${suffix}`);
}

/** Trigger a browser download of ``payload`` as pretty-printed JSON. */
function saveJson(payload: unknown, filename: string): void {
  const json = JSON.stringify(payload, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Download a bundle that is not in the database yet (the prompt box's
 * generated pipeline, cortex#1774 S5). Returns the filename used.
 */
export function downloadPipelineBundle(bundle: unknown, name: string): string {
  const filename = `${slugFromPipeline({ id: "pipeline", name })}.pipeline-bundle.json`;
  saveJson(bundle, filename);
  return filename;
}

/**
 * Convenience wrapper that catches the export error and surfaces it
 * via ``window.alert``. Pages that want richer feedback should call
 * ``downloadPipelineExport`` directly and handle the error themselves.
 */
export async function downloadPipelineExportWithAlert(
  pipeline: Pipeline,
  options: { bundle?: boolean } = {},
): Promise<void> {
  try {
    await downloadPipelineExport(pipeline, options);
  } catch (err) {
    window.alert(`Export failed: ${formatApiError(err)}`);
  }
}
