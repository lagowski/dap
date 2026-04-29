/**
 * Browser-side agent export helper (#94 / #117).
 *
 * Wraps ``GET /agents/{id}/export`` + Blob download into a single
 * call so the detail page (``/agents/[id]``) and the edit page
 * header share one implementation. Filename slug matches the agent
 * name for a git-friendly default; falls back to a short id prefix
 * if the slug ends up empty.
 */

import { exportAgent, formatApiError } from "./api/client";
import type { Agent } from "./api/types";

const ID_FALLBACK_PREFIX = 8;

function slugFromAgent(agent: Pick<Agent, "id" | "name">): string {
  return (
    agent.name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)+/g, "") || agent.id.slice(0, ID_FALLBACK_PREFIX)
  );
}

/**
 * Fetch the agent's export payload and trigger a browser download.
 * Throws when ``exportAgent`` fails — caller decides how to surface
 * (alert / toast / inline message).
 */
export async function downloadAgentExport(agent: Agent): Promise<void> {
  const payload = await exportAgent(agent.id);
  const json = JSON.stringify(payload, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${slugFromAgent(agent)}.agent.json`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Convenience wrapper that catches the export error and surfaces it
 * via ``window.alert``. Pages that want richer feedback should call
 * ``downloadAgentExport`` directly and handle the error themselves.
 */
export async function downloadAgentExportWithAlert(agent: Agent): Promise<void> {
  try {
    await downloadAgentExport(agent);
  } catch (err) {
    window.alert(`Export failed: ${formatApiError(err)}`);
  }
}
