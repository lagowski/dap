"use client";

/**
 * Presentational primitives shared across the settings sections
 * (audit D1 split). Extracted from the original ``page.tsx`` so
 * each section file imports only what it uses + the parent stays
 * a thin orchestrator.
 *
 * - ``StatusIndicator`` — green-tick / red-X badge with custom
 *   labels. Used by both runtimes (available/unavailable) and
 *   providers (key set / not set).
 * - ``CodeBlock`` — copy-to-clipboard code preview. Used in
 *   QuickSetup, RuntimeInstallHint, and ProviderEnableHint.
 * - ``Metric`` — label + value pair on a bordered card. Used by
 *   EngineSection's grid.
 */

import { useEffect, useRef, useState } from "react";
import { Check, CheckCircle2, Copy, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";


export function StatusIndicator({
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


export function CodeBlock({ code }: { code: string }) {
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


export function Metric({
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
