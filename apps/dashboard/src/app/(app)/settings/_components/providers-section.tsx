"use client";

/**
 * api-call providers table + per-provider "how to enable" hints
 * (audit D1 split).
 *
 * Like the install commands in ``runtimes-section.tsx``, the
 * per-provider key portal URLs live in the UI layer because the
 * engine doesn't ship them — operators visit the portal, generate
 * a key, then export the env var locally.
 */

import { ExternalLink } from "lucide-react";

import { Card } from "@/components/ui/card";
import type { ProviderStatus } from "@/lib/api/types";

import { CodeBlock, StatusIndicator } from "./shared";


const PROVIDER_PORTAL: Record<string, string | undefined> = {
  anthropic: "https://console.anthropic.com/settings/keys",
  openai: "https://platform.openai.com/api-keys",
  gemini: "https://aistudio.google.com/apikey",
};


export function ProvidersSection({ providers }: { providers: ProviderStatus[] }) {
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
