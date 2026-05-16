"use client";

/**
 * Top-level "how to configure DAP" disclosure card.
 *
 * Default-open so the configuration help is visible on first visit —
 * operators were missing it because the collapsed header looked like
 * just another section title rather than a disclosure with content
 * underneath.
 *
 * Extracted from ``page.tsx`` during the audit-D1 split. The two
 * ``CodeBlock``-rendered snippets are co-located here because they
 * only appear in this card.
 */

import { useId, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";

import { CodeBlock } from "./shared";

/**
 * Base URL for repo-hosted docs links. Defaults to the canonical
 * upstream repo on ``develop``; forks and pinned-branch deployments
 * can override via ``NEXT_PUBLIC_DAP_DOCS_URL`` so the links land
 * on the right files for that deployment.
 */
const DOCS_BASE_URL =
  process.env.NEXT_PUBLIC_DAP_DOCS_URL ??
  "https://github.com/rafeekpro/dap/blob/develop";

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


export function QuickSetup() {
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
