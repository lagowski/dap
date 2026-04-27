"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import { useSettings } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { EngineInfo, ProviderStatus, RuntimeStatus } from "@/lib/api/types";

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
          Read-only operator view. Configuration lives in env vars and engine
          launch flags — there&apos;s nothing to edit here.
        </p>
      </div>

      <RuntimesSection runtimes={data.runtimes} />
      <ProvidersSection providers={data.providers} />
      <EngineSection engine={data.engine} />
    </div>
  );
}

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
                <td className="px-4 py-3 text-xs text-muted-foreground">
                  {runtime.missing && runtime.missing.length > 0 ? (
                    <ul className="space-y-0.5">
                      {runtime.missing.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                  ) : (
                    "—"
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
            </tr>
          </thead>
          <tbody>
            {providers.map((provider) => (
              <tr key={provider.id} className="border-b last:border-0">
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
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </section>
  );
}

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
