"use client";

/**
 * Runtimes table + per-runtime install hints (audit D1 split).
 *
 * The install-command strings live here in the UI layer
 * intentionally — the engine doesn't ship install commands; this
 * is a documentation surface that the dashboard shows when a
 * runtime is unavailable. When DAP gains a new runtime, add an
 * entry to ``RUNTIME_INSTALL`` along with the engine-side adapter.
 */

import { ExternalLink, Terminal } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type { RuntimeStatus } from "@/lib/api/types";

import { CodeBlock, StatusIndicator } from "./shared";


interface RuntimeInstallHint {
  install: string;
  docs?: string;
  /** Optional caveat shown under the snippet (e.g. \"adapter is a stub\"). */
  note?: string;
}

const RUNTIME_INSTALL: Record<string, RuntimeInstallHint | undefined> = {
  "claude-code": {
    install: "npm install -g @anthropic-ai/claude-code",
    docs: "https://docs.claude.com/claude-code",
  },
  codex: {
    install: "npm install -g @openai/codex",
    docs: "https://github.com/openai/codex",
  },
  "gemini-cli": {
    install: "npm install -g @google/gemini-cli",
    docs: "https://github.com/google-gemini/gemini-cli",
  },
  aider: {
    install: "pip install aider-install && aider-install",
    docs: "https://aider.chat/docs/install.html",
    note: "Adapter is currently an F0 stub — install command works, but the runtime can't execute pipelines yet.",
  },
};


export function RuntimesSection({ runtimes }: { runtimes: RuntimeStatus[] }) {
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">Runtimes</h2>
      <Card>
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/50 text-left text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Runtime</th>
              <th className="px-4 py-2 font-medium">Kind</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Version</th>
              <th className="px-4 py-2 font-medium">Notes</th>
            </tr>
          </thead>
          <tbody>
            {runtimes.map((runtime) => (
              <tr key={runtime.id} className="border-b last:border-0 align-top">
                <td className="px-4 py-3">
                  <div className="font-mono text-xs">{runtime.id}</div>
                  <div className="text-xs text-muted-foreground">
                    {runtime.display_name}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <Badge variant="secondary">{runtime.kind}</Badge>
                </td>
                <td className="px-4 py-3">
                  <StatusIndicator available={runtime.available} />
                </td>
                <td className="px-4 py-3 font-mono text-xs">
                  {runtime.version || "—"}
                </td>
                <td className="px-4 py-3 text-xs text-muted-foreground space-y-2">
                  {runtime.missing && runtime.missing.length > 0 ? (
                    <ul className="space-y-0.5">
                      {runtime.missing.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                  ) : null}
                  {/* Install hint only when the runtime isn't already
                      available — for installed/working runtimes the
                      command is noise. Status icon + missing list
                      cover the unavailable case; this is the
                      reference for "what do I run to fix it". */}
                  {!runtime.available && RUNTIME_INSTALL[runtime.id] ? (
                    <RuntimeInstallHint hint={RUNTIME_INSTALL[runtime.id]!} />
                  ) : null}
                  {runtime.available && !runtime.missing?.length ? (
                    <span>—</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </section>
  );
}


function RuntimeInstallHint({ hint }: { hint: RuntimeInstallHint }) {
  // Neutral muted styling rather than amber alarm — the row's status
  // icon already says "unavailable" (callers gate render on that), so
  // this block is just the reference command, not a fresh callout.
  return (
    <div className="rounded border bg-muted/30 p-2">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        <Terminal className="h-3 w-3" aria-hidden="true" />
        Install command
      </div>
      <CodeBlock code={hint.install} />
      {hint.note ? (
        <p className="mt-1 text-[11px] text-muted-foreground italic">
          {hint.note}
        </p>
      ) : null}
      {hint.docs ? (
        <a
          href={hint.docs}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground hover:underline"
        >
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          Docs
        </a>
      ) : null}
    </div>
  );
}
