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
}

export function RuntimeConfigEditor({
  runtime_id,
  value,
  onChange,
}: RuntimeConfigEditorProps) {
  const schema = RUNTIME_SCHEMAS[runtime_id];
  const [advanced, setAdvanced] = useState(!schema);
  const [rawJson, setRawJson] = useState<string>(() =>
    JSON.stringify(value, null, 2),
  );
  const [parseError, setParseError] = useState<string | null>(null);

  // When `value` is updated from outside (e.g. runtime change resets defaults),
  // keep the raw JSON view in sync so toggling Advanced doesn't show stale text.
  useEffect(() => {
    if (!advanced) {
      setRawJson(JSON.stringify(value, null, 2));
      setParseError(null);
    }
  }, [advanced, value]);

  if (advanced || !schema) {
    return (
      <div className="space-y-2">
        <Textarea
          value={rawJson}
          onChange={(e) => {
            setRawJson(e.target.value);
            try {
              const parsed = JSON.parse(e.target.value);
              if (
                typeof parsed !== "object" ||
                parsed === null ||
                Array.isArray(parsed)
              ) {
                throw new Error("runtime_config must be a JSON object");
              }
              setParseError(null);
              onChange(parsed as Record<string, unknown>);
            } catch (err) {
              setParseError(
                err instanceof Error ? err.message : "Invalid JSON",
              );
            }
          }}
          rows={10}
          className="font-mono text-xs"
          placeholder='{"model_id": "claude-haiku-4-5", "max_tokens": 4096}'
        />
        {parseError ? (
          <p className="text-xs text-destructive" role="alert">
            {parseError}
          </p>
        ) : null}
        {schema ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setAdvanced(false)}
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

  const validationErrors = validateRuntimeConfig(runtime_id, value);
  const errorByKey = new Map(validationErrors.map((e) => [e.key, e.message]));

  const setField = (key: string, next: unknown) => {
    onChange({ ...value, [key]: next });
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
          />
        );
      })}
    </div>
  );
}

interface FieldInputProps {
  field: RuntimeField;
  value: unknown;
  onChange: (next: unknown) => void;
  error?: string;
}

function FieldInput({ field, value, onChange, error }: FieldInputProps) {
  const id = `runtime-config-${field.key}`;
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs">
        {field.label}
        {field.required ? <span className="text-destructive ml-0.5">*</span> : null}
      </Label>
      {renderInput({ id, field, value, onChange })}
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
}: {
  id: string;
  field: RuntimeField;
  value: unknown;
  onChange: (next: unknown) => void;
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
        <Textarea
          id={id}
          value={
            value === undefined ? "" : JSON.stringify(value, null, 2)
          }
          placeholder={field.placeholder}
          onChange={(e) => {
            const text = e.target.value;
            if (text.trim() === "") {
              onChange(undefined);
              return;
            }
            try {
              onChange(JSON.parse(text));
            } catch {
              // Keep the raw text as the user types — Zod schema will
              // surface the error on submit. Storing the unparseable
              // string here lets the textarea show what the user typed.
              onChange(text);
            }
          }}
          rows={4}
          className="font-mono text-xs"
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

function KeyValueEditor({
  id,
  value,
  onChange,
}: {
  id: string;
  value: Record<string, string>;
  onChange: (next: Record<string, string> | undefined) => void;
}) {
  const entries = Object.entries(value);

  const setEntry = (idx: number, key: string, val: string) => {
    const next: Record<string, string> = {};
    entries.forEach(([k, v], i) => {
      if (i === idx) {
        if (key) next[key] = val;
      } else {
        next[k] = v;
      }
    });
    onChange(Object.keys(next).length === 0 ? undefined : next);
  };

  const removeEntry = (idx: number) => {
    const next: Record<string, string> = {};
    entries.forEach(([k, v], i) => {
      if (i !== idx) next[k] = v;
    });
    onChange(Object.keys(next).length === 0 ? undefined : next);
  };

  const addEntry = () => {
    onChange({ ...value, "": "" });
  };

  return (
    <div id={id} className="space-y-1.5">
      {entries.map(([key, val], idx) => (
        <div key={`${idx}-${key}`} className="flex items-center gap-1.5">
          <Input
            value={key}
            placeholder="KEY"
            onChange={(e) => setEntry(idx, e.target.value, val)}
            className="h-7 text-xs flex-1 font-mono"
          />
          <Input
            value={val}
            placeholder="value"
            onChange={(e) => setEntry(idx, key, e.target.value)}
            className="h-7 text-xs flex-1"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            onClick={() => removeEntry(idx)}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={addEntry}>
        Add entry
      </Button>
    </div>
  );
}
