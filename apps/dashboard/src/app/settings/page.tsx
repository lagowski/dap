"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Check, CheckCircle2, ChevronDown, ChevronRight, Copy, ExternalLink, Terminal, XCircle } from "lucide-react";
import { useSettings } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { EngineInfo, ProviderStatus, RuntimeStatus } from "@/lib/api/types";

/**
 * Base URL for repo-hosted docs links. Defaults to the canonical
 * upstream repo on ``develop``; forks and pinned-branch deployments
 * can override via ``NEXT_PUBLIC_DAP_DOCS_URL`` so the links land
 * on the right files for that deployment.
 */
const DOCS_BASE_URL =
  process.env.NEXT_PUBLIC_DAP_DOCS_URL ??
  "https://github.com/rafeekpro/dap/blob/develop";

export default function SettingsPage() {
  const { data, isPending, isError, error } = useSettings();

  if (isPending) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }
  if (isError) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {formatApiError(error)}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Read-only operator view. Configuration lives in env vars and binaries
          on PATH — apply changes outside DAP and restart the engine to pick
          them up. Expand the sections below for the exact shell snippets.
        </p>
      </div>

      <QuickSetup />
      <RuntimesSection runtimes={data.runtimes} />
      <ProvidersSection providers={data.providers} />
      <EngineSection engine={data.engine} />
    </div>
  );
}

// --------------------------------------------------------------------------
// Quick setup card — top-level "how to configure" guide.
// --------------------------------------------------------------------------

const ENV_SNIPPET = `# 1. Put keys in .env.local (gitignored — see .env.example for the full list)
cat > .env.local <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AI...
GLM_API_KEY=...   # Z.AI GLM, optional
EOF

# 2. Load + start the engine
set -a; source .env.local; set +a
uv run dap-engine start`;

const OPENAI_COMPAT_SNIPPET = `# Pick provider: "openai-compat" in the agent form, then:
#   base_url:     https://your-endpoint/v1   (Together, OpenRouter, …)
#   api_key_env:  YOUR_PROVIDER_API_KEY     (the env var name, not the key)

# And export the matching env var on the engine before restart:
export YOUR_PROVIDER_API_KEY=...`;

function QuickSetup() {
  // Default to open so the configuration help is visible on first
  // visit — operators were missing it because the collapsed header
  // looked like just another section title rather than a disclosure
  // with content underneath.
  const [open, setOpen] = useState(true);
  const contentId = useId();
  return (
    <section className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={contentId}
        className="flex w-full items-center gap-2 text-lg font-medium hover:text-foreground/80"
      >
        {open ? (
          <ChevronDown className="h-4 w-4" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        )}
        Quick setup
      </button>
      {open ? (
        <Card id={contentId}>
          <CardContent className="pt-6 space-y-4 text-sm">
            <ol className="list-decimal list-inside space-y-3">
              <li>
                <span className="font-medium">Provider keys</span> live in the
                engine&apos;s process env. The engine reads them at startup
                only — restart after changes.
                <CodeBlock code={ENV_SNIPPET} />
                <p className="text-xs text-muted-foreground">
                  Per-row links below open the portal where you generate each
                  key.
                </p>
              </li>
              <li>
                <span className="font-medium">CLI runtimes</span> need a binary
                on PATH. Install commands appear under each unavailable runtime
                in the table below. CLIs read the same env vars as the SDK
                providers — no separate setup.
              </li>
              <li>
                <span className="font-medium">Per-agent params</span>{" "}
                (model_id, max_tokens, prompt_cache, etc.) live on the agent
                in <code className="font-mono text-xs">runtime_config</code> —
                edit via{" "}
                <span className="font-mono text-xs">/agents/new</span> or{" "}
                <span className="font-mono text-xs">/agents/[id]/edit</span>.
                Settings here only shows <em>availability</em>, not what your
                agents do with it.
              </li>
              <li>
                <span className="font-medium">Project context</span>{" "}
                (working_directory, env_vars overlay, default branch) lives on
                the project — edit via{" "}
                <span className="font-mono text-xs">/projects/[id]/edit</span>.
                See{" "}
                <a
                  className="underline hover:text-foreground"
                  href={`${DOCS_BASE_URL}/docs/projects.md`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  docs/projects.md
                </a>{" "}
                for the env-layering rules.
              </li>
              <li>
                <span className="font-medium">Custom OpenAI-compatible
                providers</span>{" "}
                (Together, OpenRouter, internal proxies, llama.cpp, …) — pick{" "}
                <code className="font-mono text-xs">provider: openai-compat</code>{" "}
                on the agent form, supply the endpoint{" "}
                <code className="font-mono text-xs">base_url</code> and the env
                var name in{" "}
                <code className="font-mono text-xs">api_key_env</code>, then
                export the matching env on the engine. Z.AI GLM is registered
                as a first-class provider — use{" "}
                <code className="font-mono text-xs">provider: glm</code> with
                just{" "}
                <code className="font-mono text-xs">GLM_API_KEY</code> in env;
                the URL is hardcoded.
                <CodeBlock code={OPENAI_COMPAT_SNIPPET} />
              </li>
            </ol>
            <div className="border-t pt-3 text-xs text-muted-foreground">
              Full reference:{" "}
              <a
                className="underline hover:text-foreground"
                href={`${DOCS_BASE_URL}/docs/providers.md`}
                target="_blank"
                rel="noopener noreferrer"
              >
                docs/providers.md
              </a>{" "}
              (per-provider setup) ·{" "}
              <a
                className="underline hover:text-foreground"
                href={`${DOCS_BASE_URL}/packages/runtimes/README.md`}
                target="_blank"
                rel="noopener noreferrer"
              >
                packages/runtimes/README.md
              </a>{" "}
              (per-runtime config keys + env layering)
            </div>
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}

// --------------------------------------------------------------------------
// Per-runtime install hints. Strings are kept here (UI-side) intentionally —
// the engine doesn't ship install commands; this is a documentation surface.
// --------------------------------------------------------------------------

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

function RuntimesSection({ runtimes }: { runtimes: RuntimeStatus[] }) {
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

// --------------------------------------------------------------------------
// Per-provider key portal links.
// --------------------------------------------------------------------------

const PROVIDER_PORTAL: Record<string, string | undefined> = {
  anthropic: "https://console.anthropic.com/settings/keys",
  openai: "https://platform.openai.com/api-keys",
  gemini: "https://aistudio.google.com/apikey",
};

function ProvidersSection({ providers }: { providers: ProviderStatus[] }) {
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">api-call providers</h2>
      <Card>
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/50 text-left text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Provider</th>
              <th className="px-4 py-2 font-medium">API key env</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">How to enable</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((provider) => (
              <tr key={provider.id} className="border-b last:border-0 align-top">
                <td className="px-4 py-3">
                  <div className="font-mono text-xs">{provider.id}</div>
                  <div className="text-xs text-muted-foreground">
                    {provider.display_name}
                  </div>
                </td>
                <td className="px-4 py-3 font-mono text-xs">
                  {provider.default_env_var || (
                    <span className="text-muted-foreground italic">
                      per-agent
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {provider.default_env_var === null ? (
                    <span className="text-xs text-muted-foreground">
                      configured per-agent in <code>runtime_config</code>
                    </span>
                  ) : (
                    <StatusIndicator
                      available={provider.configured}
                      labelOk="key set"
                      labelMissing="key not set"
                    />
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-muted-foreground">
                  {provider.default_env_var === null ? (
                    <span>
                      Set <code className="font-mono">api_key_env</code> on the
                      agent&apos;s <code>runtime_config</code> and export the
                      env var before starting the engine.
                    </span>
                  ) : provider.configured ? (
                    "—"
                  ) : (
                    <ProviderEnableHint
                      providerId={provider.id}
                      envVar={provider.default_env_var}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </section>
  );
}

function ProviderEnableHint({
  providerId,
  envVar,
}: {
  providerId: string;
  envVar: string;
}) {
  const portal = PROVIDER_PORTAL[providerId];
  return (
    <div className="space-y-1.5">
      <div>
        Add to <code className="font-mono">.env.local</code> and restart the
        engine:
      </div>
      <CodeBlock code={`${envVar}=…`} />
      {portal ? (
        <a
          href={portal}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[11px] hover:underline"
        >
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          Generate a key
        </a>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------
// Engine block (unchanged).
// --------------------------------------------------------------------------

function EngineSection({ engine }: { engine: EngineInfo }) {
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">Engine</h2>
      <Card>
        <CardContent className="pt-6 grid grid-cols-2 gap-4 text-xs">
          <Metric label="Version" value={engine.version} mono />
          <Metric label="Recursion limit" value={String(engine.recursion_limit)} />
          <Metric label="DB" value={engine.db_path} mono />
          <Metric label="Checkpoint DB" value={engine.checkpoint_db_path} mono />
        </CardContent>
      </Card>
    </section>
  );
}

// --------------------------------------------------------------------------
// Shared bits.
// --------------------------------------------------------------------------

function StatusIndicator({
  available,
  labelOk = "available",
  labelMissing = "unavailable",
}: {
  available: boolean;
  labelOk?: string;
  labelMissing?: string;
}) {
  return available ? (
    <span className="inline-flex items-center gap-1 text-xs">
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
      {labelOk}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-xs text-destructive">
      <XCircle className="h-3.5 w-3.5" />
      {labelMissing}
    </span>
  );
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  // Track the active "reset to Copy icon" timer so it can be cleared
  // both when the user copies again rapidly and when the component
  // unmounts mid-flight — avoids state-update-after-unmount warnings
  // and the small timer leak that comes with it.
  const timeoutRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
    },
    [],
  );

  const onCopy = async () => {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = window.setTimeout(() => {
        setCopied(false);
        timeoutRef.current = null;
      }, 1500);
    } catch {
      // Clipboard blocked — silently fail; the code is selectable anyway.
    }
  };
  return (
    <div className="relative mt-1">
      <pre className="font-mono text-[11px] bg-muted/60 rounded p-2 pr-8 overflow-x-auto whitespace-pre">
        {code}
      </pre>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={onCopy}
        aria-label="Copy to clipboard"
        className="absolute top-1 right-1 h-6 w-6 p-0"
      >
        {copied ? (
          <Check className="h-3 w-3" aria-hidden="true" />
        ) : (
          <Copy className="h-3 w-3" aria-hidden="true" />
        )}
      </Button>
    </div>
  );
}

function Metric({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded border bg-background p-3">
      <div className="text-muted-foreground">{label}</div>
      <div
        className={`font-medium break-all ${mono ? "font-mono text-[11px]" : ""}`}
      >
        {value}
      </div>
    </div>
  );
}
