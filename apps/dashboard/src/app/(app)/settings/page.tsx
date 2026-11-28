"use client";

/**
 * Settings page orchestrator (audit D1 slice 2).
 *
 * Post-split this file just handles loading / error states and
 * composes the four sub-sections; all the actual rendering lives
 * under ``_components/`` (Next.js convention: underscore-prefixed
 * dirs are private, not routable):
 *
 * - ``shared.tsx``              — primitives (StatusIndicator,
 *                                 CodeBlock, Metric)
 * - ``quick-setup.tsx``         — onboarding disclosure card
 * - ``runtimes-section.tsx``    — runtimes table + install hints
 * - ``providers-section.tsx``   — api-call providers table + key
 *                                 portal hints
 * - ``engine-section.tsx``      — version / DB / recursion-cap
 *                                 metadata
 *
 * Pre-split this file was 551 LOC; post-split it stays well under
 * 80.
 */

import { Card, CardContent } from "@/components/ui/card";
import { LoadingState } from "@/components/ui/spinner";
import { useSettings } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";

import { EngineSection } from "./_components/engine-section";
import { ProvidersSection } from "./_components/providers-section";
import { QuickSetup } from "./_components/quick-setup";
import { RuntimesSection } from "./_components/runtimes-section";


export default function SettingsPage() {
  const { data, isPending, isError, error } = useSettings();

  if (isPending) {
    return <LoadingState className="p-6" />;
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
