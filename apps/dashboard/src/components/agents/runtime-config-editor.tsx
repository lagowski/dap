"use client";

import { useEffect, useState } from "react";
import { Code2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  type RuntimeField,
  RUNTIME_SCHEMAS,
  validateRuntimeConfig,
} from "./runtime-config-schemas";

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

interface AdvancedJsonEditorProps {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  onValidityChange: (valid: boolean) => void;
  canSwitchBack: boolean;
  onSwitchBack: () => void;
  runtime_id: string;
}

function AdvancedJsonEditor({
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

interface FieldInputProps {
  field: RuntimeField;
  value: unknown;
  onChange: (next: unknown) => void;
  error?: string;
  onJsonError: (message: string | null) => void;
}

function FieldInput({
  field,
  value,
  onChange,
  error,
  onJsonError,
}: FieldInputProps) {
  const id = `runtime-config-${field.key}`;
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs">
        {field.label}
        {field.required ? <span className="text-destructive ml-0.5">*</span> : null}
      </Label>
      {renderInput({ id, field, value, onChange, onJsonError })}
      {field.description ? (
        <p className="text-[11px] text-muted-foreground">{field.description}</p>
      ) : null}
      {error ? (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function renderInput({
  id,
  field,
  value,
  onChange,
  onJsonError,
}: {
  id: string;
  field: RuntimeField;
  value: unknown;
  onChange: (next: unknown) => void;
  onJsonError: (message: string | null) => void;
}): React.ReactNode {
  switch (field.kind) {
    case "text":
      return (
        <Input
          id={id}
          value={typeof value === "string" ? value : ""}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 text-xs"
        />
      );

    case "number":
      return (
        <Input
          id={id}
          type="number"
          value={typeof value === "number" ? value : ""}
          placeholder={field.placeholder}
          onChange={(e) => {
            const n = e.target.value === "" ? undefined : Number(e.target.value);
            onChange(n);
          }}
          className="h-8 text-xs"
        />
      );

    case "boolean":
      return (
        <label className="inline-flex items-center gap-2 text-xs cursor-pointer select-none">
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            onChange={(e) => onChange(e.target.checked)}
            className="rounded"
          />
          <span>Enabled</span>
        </label>
      );

    case "select":
      return (
        <select
          id={id}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value || undefined)}
          className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
        >
          {field.options?.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      );

    case "json":
      return (
        <JsonFieldInput
          id={id}
          field={field}
          value={value}
          onChange={onChange}
          onError={onJsonError}
        />
      );

    case "kv":
      return (
        <KeyValueEditor
          id={id}
          value={
            value && typeof value === "object" && !Array.isArray(value)
              ? (value as Record<string, string>)
              : {}
          }
          onChange={onChange}
        />
      );
  }
}

interface JsonFieldInputProps {
  id: string;
  field: RuntimeField;
  value: unknown;
  onChange: (next: unknown) => void;
  onError: (message: string | null) => void;
}

function JsonFieldInput({
  id,
  field,
  value,
  onChange,
  onError,
}: JsonFieldInputProps) {
  // Local text state so the textarea preserves what the user typed even
  // when it doesn't parse. Without this we'd `JSON.stringify` whatever
  // is in `value`, which would round-trip through quoting if the catch
  // path stashed an unparseable string there.
  const [text, setText] = useState<string>(() =>
    value === undefined ? "" : JSON.stringify(value, null, 2),
  );
  const [parseError, setParseError] = useState<string | null>(null);

  // Sync from outside when `value` changes externally (runtime swap, etc.)
  // Skip syncs that match the parsed local text — we'd flicker otherwise.
  useEffect(() => {
    const incoming = value === undefined ? "" : JSON.stringify(value, null, 2);
    let parsedLocal: unknown = undefined;
    try {
      parsedLocal = text.trim() === "" ? undefined : JSON.parse(text);
    } catch {
      parsedLocal = Symbol("unparseable");
    }
    if (JSON.stringify(parsedLocal) !== JSON.stringify(value)) {
      setText(incoming);
      setParseError(null);
      onError(null);
    }
    // We intentionally don't depend on `text` to avoid loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, onError]);

  const handleChange = (raw: string) => {
    setText(raw);
    if (raw.trim() === "") {
      setParseError(null);
      onError(null);
      onChange(undefined);
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      setParseError(null);
      onError(null);
      onChange(parsed);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Invalid JSON";
      setParseError(message);
      onError(message);
      // Don't write the raw string into runtime_config — keep the
      // last-known-good value to avoid leaking unparseable text into
      // the submit payload.
    }
  };

  return (
    <div className="space-y-1">
      <Textarea
        id={id}
        value={text}
        placeholder={field.placeholder}
        onChange={(e) => handleChange(e.target.value)}
        rows={4}
        className="font-mono text-xs"
      />
      {parseError ? (
        <p className="text-xs text-destructive" role="alert">
          {parseError}
        </p>
      ) : null}
    </div>
  );
}

interface KeyValueRow {
  key: string;
  value: string;
}

function KeyValueEditor({
  id,
  value,
  onChange,
}: {
  id: string;
  value: Record<string, string>;
  onChange: (next: Record<string, string> | undefined) => void;
}) {
  // Internal array-of-rows so editing a key doesn't risk colliding with
  // an existing key (or losing the row mid-edit when the key is empty).
  // We rebuild the dict on every change and propagate up — duplicate
  // keys get a visible inline warning instead of silent overwrite.
  const [rows, setRows] = useState<KeyValueRow[]>(() =>
    Object.entries(value).map(([k, v]) => ({ key: k, value: v })),
  );

  // Sync from outside when `value` is replaced (e.g. runtime change reset).
  useEffect(() => {
    const externalEntries = Object.entries(value);
    const localAsObject = rowsToObject(rows);
    if (JSON.stringify(localAsObject) !== JSON.stringify(value)) {
      setRows(externalEntries.map(([k, v]) => ({ key: k, value: v })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const updateRows = (next: KeyValueRow[]) => {
    setRows(next);
    const obj = rowsToObject(next);
    onChange(Object.keys(obj).length === 0 ? undefined : obj);
  };

  const setRow = (idx: number, patch: Partial<KeyValueRow>) => {
    updateRows(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  const removeRow = (idx: number) => {
    updateRows(rows.filter((_, i) => i !== idx));
  };

  const addRow = () => {
    updateRows([...rows, { key: "", value: "" }]);
  };

  const duplicateKeys = findDuplicateKeys(rows);

  return (
    <div id={id} className="space-y-1.5">
      {rows.map((row, idx) => {
        const isDuplicate =
          row.key.length > 0 && duplicateKeys.has(row.key);
        return (
          <div key={idx} className="space-y-0.5">
            <div className="flex items-center gap-1.5">
              <Input
                value={row.key}
                placeholder="KEY"
                onChange={(e) => setRow(idx, { key: e.target.value })}
                className={`h-7 text-xs flex-1 font-mono ${
                  isDuplicate ? "border-destructive" : ""
                }`}
              />
              <Input
                value={row.value}
                placeholder="value"
                onChange={(e) => setRow(idx, { value: e.target.value })}
                className="h-7 text-xs flex-1"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={() => removeRow(idx)}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
            {isDuplicate ? (
              <p className="text-[11px] text-destructive">
                Duplicate key — only the last value survives.
              </p>
            ) : null}
          </div>
        );
      })}
      <Button type="button" variant="outline" size="sm" onClick={addRow}>
        Add entry
      </Button>
    </div>
  );
}

function rowsToObject(rows: KeyValueRow[]): Record<string, string> {
  // Last-write-wins on duplicate keys — UI surfaces the warning so the
  // user sees the conflict before submit.
  const obj: Record<string, string> = {};
  for (const row of rows) {
    if (row.key) obj[row.key] = row.value;
  }
  return obj;
}

function findDuplicateKeys(rows: KeyValueRow[]): Set<string> {
  const seen = new Set<string>();
  const dupes = new Set<string>();
  for (const row of rows) {
    if (!row.key) continue;
    if (seen.has(row.key)) dupes.add(row.key);
    seen.add(row.key);
  }
  return dupes;
}
