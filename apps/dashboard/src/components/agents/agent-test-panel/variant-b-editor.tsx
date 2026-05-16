"use client";

/**
 * Variant-A summary + Variant-B editor extracted from
 * ``AgentTestPanel`` (audit D1). Only used in compare mode; the
 * single-mode column shows the same Variant A under "Run test"
 * without these pieces.
 *
 * The editor owns its own textarea state for ``runtime_config``
 * (JSON text is debounced against the parent so a partially-typed
 * brace doesn't immediately mark the variant as invalid) but
 * everything else is driven by the parent via ``value`` / ``onChange``.
 */

import { useEffect, useRef, useState } from "react";

import { Textarea } from "@/components/ui/textarea";
import type { AgentDryRunDraft } from "@/lib/api/types";

import { AGENT_RUNTIME_IDS } from "../runtime-config-schemas";

import {
  type VariantBOverrides,
  VARIANT_A_PROMPT_LIMIT,
  stableStringify,
} from "./diff-utils";


export function VariantASummary({ draft }: { draft: AgentDryRunDraft | null }) {
  if (!draft) {
    return (
      <p className="text-xs text-muted-foreground">
        Fill in the form to populate Variant A.
      </p>
    );
  }
  const modelId =
    typeof draft.runtime_config["model_id"] === "string"
      ? (draft.runtime_config["model_id"] as string)
      : null;
  return (
    <div className="rounded border bg-muted/30 p-2 space-y-1.5 text-xs">
      <SummaryRow label="runtime_id" value={draft.runtime_id} />
      {modelId ? <SummaryRow label="model_id" value={modelId} /> : null}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          prompt_template (excerpt)
        </summary>
        <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px]">
          {draft.prompt_template.length > VARIANT_A_PROMPT_LIMIT
            ? `${draft.prompt_template.slice(0, VARIANT_A_PROMPT_LIMIT)}…`
            : draft.prompt_template}
        </pre>
      </details>
    </div>
  );
}


function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="font-mono">{value}</span>
    </div>
  );
}


export function VariantBEditor({
  value,
  onChange,
  onJsonValidityChange,
  diffs,
}: {
  value: VariantBOverrides | null;
  onChange: (next: VariantBOverrides) => void;
  onJsonValidityChange: (valid: boolean) => void;
  diffs: Set<keyof VariantBOverrides>;
}) {
  // Hooks before any conditional return — rules-of-hooks. The empty
  // string + ``null`` guards inside the body keep the editor inert when
  // ``value`` is null (compare just toggled on with an invalid form).
  const initialJson = value ? JSON.stringify(value.runtime_config, null, 2) : "";
  const [runtimeConfigText, setRuntimeConfigText] = useState(initialJson);
  const [runtimeConfigError, setRuntimeConfigError] = useState<string | null>(
    null,
  );

  // Re-sync the textarea when the parent rebuilds Variant B's
  // runtime_config (e.g. after compare toggle re-init). Two
  // properties we want here:
  //
  //   1. Run on parent-side runtime_config changes, NOT on every
  //      keystroke into the textarea (a keystroke updates
  //      ``runtimeConfigText`` but the parent's runtime_config is
  //      what we sync FROM, not TO).
  //   2. Don't fire the lint waiver gun. Earlier shape suppressed
  //      ``react-hooks/exhaustive-deps`` because the effect read
  //      ``runtimeConfigText`` without listing it; council follow-
  //      up (#460) flagged the suppression as a stale-closure
  //      risk.
  //
  // Mechanism: depend on ``value`` (which eslint happily accepts),
  // and guard the body with a stable-signature comparison against
  // the previous runtime_config. The parent passes a new ``value``
  // object on every edit, but the structural signature is what
  // determines whether we actually need to re-sync. ``stableStringify``
  // ignores key order so a re-ordered-but-identical config doesn't
  // clobber the user's text. The current textarea contents are read
  // from a ref so the effect doesn't have to list it as a dep
  // either.
  const runtimeConfigTextRef = useRef(runtimeConfigText);
  runtimeConfigTextRef.current = runtimeConfigText;
  const prevRuntimeConfigSig = useRef<string | null>(null);
  useEffect(() => {
    if (!value) {
      prevRuntimeConfigSig.current = null;
      return;
    }
    const currentSig = stableStringify(value.runtime_config);
    if (currentSig === prevRuntimeConfigSig.current) return;
    prevRuntimeConfigSig.current = currentSig;

    const expected = JSON.stringify(value.runtime_config, null, 2);
    const current = runtimeConfigTextRef.current;
    if (expected === current) return;
    try {
      const parsed: unknown = JSON.parse(current);
      if (stableStringify(parsed) !== currentSig) {
        setRuntimeConfigText(expected);
      }
    } catch {
      setRuntimeConfigText(expected);
    }
  }, [value]);

  // Mirror the JSON parse status up to the parent so "Run both" can
  // gate on it. ``onJsonValidityChange`` runs on every flip rather
  // than every keystroke so we don't churn the parent unnecessarily.
  const wasValid = runtimeConfigError === null;
  useEffect(() => {
    onJsonValidityChange(wasValid);
  }, [wasValid, onJsonValidityChange]);

  if (!value) {
    return (
      <p className="text-xs text-muted-foreground">
        Variant B will mirror Variant A once you toggle compare on with a
        valid form.
      </p>
    );
  }

  const updateRuntimeConfig = (text: string) => {
    setRuntimeConfigText(text);
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        setRuntimeConfigError("runtime_config must be a JSON object");
        return;
      }
      setRuntimeConfigError(null);
      onChange({ ...value, runtime_config: parsed as Record<string, unknown> });
    } catch (err) {
      setRuntimeConfigError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="rounded border bg-muted/10 p-2 space-y-2">
      <FieldLabel
        label="runtime_id"
        diverged={diffs.has("runtime_id")}
      >
        <select
          value={value.runtime_id}
          onChange={(e) => onChange({ ...value, runtime_id: e.target.value })}
          className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
        >
          {AGENT_RUNTIME_IDS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </FieldLabel>

      <FieldLabel
        label="runtime_config (JSON)"
        diverged={diffs.has("runtime_config")}
      >
        <Textarea
          value={runtimeConfigText}
          onChange={(e) => updateRuntimeConfig(e.target.value)}
          rows={6}
          className="font-mono text-[11px]"
        />
        {runtimeConfigError ? (
          <p className="text-xs text-destructive">{runtimeConfigError}</p>
        ) : null}
      </FieldLabel>

      <FieldLabel
        label="prompt_template"
        diverged={diffs.has("prompt_template")}
      >
        <Textarea
          value={value.prompt_template}
          onChange={(e) =>
            onChange({ ...value, prompt_template: e.target.value })
          }
          rows={6}
          className="font-mono text-[11px]"
        />
      </FieldLabel>
    </div>
  );
}


function FieldLabel({
  label,
  diverged,
  children,
}: {
  label: string;
  diverged: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        {diverged ? (
          <span className="rounded bg-amber-500/15 px-1 text-[10px] font-medium uppercase text-amber-700 dark:text-amber-500">
            changed
          </span>
        ) : null}
      </div>
      {children}
    </div>
  );
}
