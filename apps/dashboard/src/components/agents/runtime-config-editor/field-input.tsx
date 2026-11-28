"use client";

/**
 * Per-field renderer for schema-declared runtime config fields
 * (audit D1 split). Dispatches by ``field.kind`` to text / number /
 * boolean / select inputs inline, and delegates JSON + key-value
 * fields to their dedicated sub-components.
 */

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import type { RuntimeField } from "../runtime-config-schemas";

import { JsonFieldInput } from "./json-field-input";
import { KeyValueEditor } from "./key-value-editor";


interface FieldInputProps {
  field: RuntimeField;
  value: unknown;
  onChange: (next: unknown) => void;
  error?: string;
  onJsonError: (message: string | null) => void;
}


export function FieldInput({
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
