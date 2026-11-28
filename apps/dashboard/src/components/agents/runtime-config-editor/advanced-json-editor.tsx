"use client";

/**
 * Raw-JSON editor for ``runtime_config`` (audit D1 split).
 *
 * Used when the parent editor has no per-field schema for the
 * selected runtime, OR when the user clicks "Advanced (raw JSON)"
 * to bypass the per-field UI on a schema-known runtime.
 *
 * Owns its own textarea text so a partially-typed brace doesn't
 * immediately invalidate the parent's value — the parent stays at
 * last-known-good while parsing fails, and re-syncs once parse
 * succeeds. Mirrors the same UX the per-field JSON editor uses.
 */

import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";


interface AdvancedJsonEditorProps {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  onValidityChange: (valid: boolean) => void;
  canSwitchBack: boolean;
  onSwitchBack: () => void;
  runtime_id: string;
}


export function AdvancedJsonEditor({
  value,
  onChange,
  onValidityChange,
  canSwitchBack,
  onSwitchBack,
  runtime_id,
}: AdvancedJsonEditorProps) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);

  // Sync local text when `value` is replaced from outside (e.g. runtime
  // change resets defaults).
  useEffect(() => {
    setText(JSON.stringify(value, null, 2));
    setParseError(null);
    onValidityChange(true);
  }, [value, onValidityChange]);

  const handleChange = (raw: string) => {
    setText(raw);
    if (raw.trim() === "") {
      setParseError(null);
      onValidityChange(true);
      onChange({});
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      if (
        typeof parsed !== "object" ||
        parsed === null ||
        Array.isArray(parsed)
      ) {
        throw new Error("runtime_config must be a JSON object");
      }
      setParseError(null);
      onValidityChange(true);
      onChange(parsed as Record<string, unknown>);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Invalid JSON";
      setParseError(message);
      // Block submit while invalid; leave `value` at last-known-good so
      // toggling back to fields mode shows consistent state.
      onValidityChange(false);
    }
  };

  return (
    <div className="space-y-2">
      <Textarea
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        rows={10}
        className="font-mono text-xs"
        placeholder='{"model_id": "claude-haiku-4-5", "max_tokens": 4096}'
      />
      {parseError ? (
        <p className="text-xs text-destructive" role="alert">
          {parseError}
        </p>
      ) : null}
      {canSwitchBack ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onSwitchBack}
        >
          <X className="h-3.5 w-3.5 mr-1" />
          Back to fields
        </Button>
      ) : (
        <p className="text-xs text-muted-foreground">
          No schema declared for runtime <code>{runtime_id}</code> — using raw
          JSON.
        </p>
      )}
    </div>
  );
}
