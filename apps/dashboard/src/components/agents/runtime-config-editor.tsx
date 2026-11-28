"use client";

/**
 * Runtime config editor orchestrator (audit D1 slice 3).
 *
 * Post-split this file owns ONLY the top-level component, the
 * advanced-mode toggle, and the per-child validity aggregation.
 * Presentational pieces live under
 * ``components/agents/runtime-config-editor/``:
 *
 * - ``advanced-json-editor.tsx`` — raw-JSON textarea fallback
 * - ``field-input.tsx``          — per-field renderer (dispatches
 *                                  by ``field.kind``)
 * - ``json-field-input.tsx``     — JSON textarea for schema fields
 *                                  of ``kind: "json"``
 * - ``key-value-editor.tsx``     — string-dict editor for
 *                                  ``kind: "kv"`` fields
 *
 * Pre-split this file was 547 LOC; post-split it stays under 130.
 */

import { useEffect, useState } from "react";

import { Code2 } from "lucide-react";

import { Button } from "@/components/ui/button";

import { AdvancedJsonEditor } from "./runtime-config-editor/advanced-json-editor";
import { FieldInput } from "./runtime-config-editor/field-input";
import { RUNTIME_SCHEMAS, validateRuntimeConfig } from "./runtime-config-schemas";


interface RuntimeConfigEditorProps {
  runtime_id: string;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  /**
   * Reports whether the editor's current state would produce a valid
   * payload — false when JSON inputs (Advanced mode or per-field json)
   * fail to parse. AgentForm uses this to gate submission.
   */
  onValidityChange?: (valid: boolean) => void;
}


export function RuntimeConfigEditor({
  runtime_id,
  value,
  onChange,
  onValidityChange,
}: RuntimeConfigEditorProps) {
  const schema = RUNTIME_SCHEMAS[runtime_id];

  // Toggle Advanced JSON mode. Reset whenever runtime changes so a
  // user who switches from an unknown runtime → a known one doesn't
  // get stuck in raw-JSON mode (and vice-versa).
  const [advanced, setAdvanced] = useState(!schema);
  useEffect(() => {
    setAdvanced(!schema);
  }, [runtime_id, schema]);

  // Track validity contributions from children so we can propagate up.
  const [advancedValid, setAdvancedValid] = useState(true);
  const [jsonFieldErrors, setJsonFieldErrors] = useState<
    Record<string, string | null>
  >({});

  useEffect(() => {
    const fieldsValid = Object.values(jsonFieldErrors).every((e) => e == null);
    onValidityChange?.(advancedValid && fieldsValid);
  }, [advancedValid, jsonFieldErrors, onValidityChange]);

  if (advanced || !schema) {
    return (
      <AdvancedJsonEditor
        value={value}
        onChange={onChange}
        onValidityChange={setAdvancedValid}
        canSwitchBack={Boolean(schema)}
        onSwitchBack={() => setAdvanced(false)}
        runtime_id={runtime_id}
      />
    );
  }

  const validationErrors = validateRuntimeConfig(runtime_id, value);
  const errorByKey = new Map(validationErrors.map((e) => [e.key, e.message]));

  const setField = (key: string, next: unknown) => {
    if (next === undefined) {
      const { [key]: _drop, ...rest } = value;
      onChange(rest);
    } else {
      onChange({ ...value, [key]: next });
    }
  };

  const setJsonFieldError = (key: string, message: string | null) => {
    setJsonFieldErrors((prev) => {
      if (prev[key] === message) return prev;
      return { ...prev, [key]: message };
    });
  };

  return (
    <div className="space-y-3 rounded border bg-muted/20 p-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Fields below populate <code>runtime_config</code>. Switch runtimes to
          see different fields.
        </p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setAdvanced(true)}
        >
          <Code2 className="h-3.5 w-3.5 mr-1" />
          Advanced (raw JSON)
        </Button>
      </div>

      {schema.fields.map((field) => {
        const visible = field.visible ? field.visible(value) : true;
        if (!visible) return null;
        return (
          <FieldInput
            key={field.key}
            field={field}
            value={value[field.key]}
            onChange={(next) => setField(field.key, next)}
            error={errorByKey.get(field.key)}
            onJsonError={(msg) => setJsonFieldError(field.key, msg)}
          />
        );
      })}
    </div>
  );
}
