"use client";

/**
 * Per-field JSON textarea for schema-declared ``kind: "json"`` fields
 * (audit D1 split).
 *
 * Same UX shape as ``AdvancedJsonEditor`` but scoped to one field
 * inside a structured form: own textarea text so partial typing
 * doesn't clobber the parent value, parse errors surface inline,
 * the parent learns about validity via ``onError``.
 */

import { useEffect, useRef, useState } from "react";

import { Textarea } from "@/components/ui/textarea";

import type { RuntimeField } from "../runtime-config-schemas";


interface JsonFieldInputProps {
  id: string;
  field: RuntimeField;
  value: unknown;
  onChange: (next: unknown) => void;
  onError: (message: string | null) => void;
}


export function JsonFieldInput({
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

  // Sync from outside when `value` changes externally (runtime swap,
  // etc.). We need to read the current textarea contents to decide
  // whether the incoming ``value`` actually differs from what the user
  // is typing — that read goes through a ref so we don't have to list
  // ``text`` as a dep (which would loop the effect on every keystroke).
  // The ref mirrors the latest ``text`` value via the inline
  // assignment on every render — same value the closure would've seen,
  // just not via a stale-prone closure capture.
  const textRef = useRef(text);
  textRef.current = text;
  useEffect(() => {
    const incoming = value === undefined ? "" : JSON.stringify(value, null, 2);
    const current = textRef.current;
    let parsedLocal: unknown = undefined;
    try {
      parsedLocal = current.trim() === "" ? undefined : JSON.parse(current);
    } catch {
      parsedLocal = Symbol("unparseable");
    }
    if (JSON.stringify(parsedLocal) !== JSON.stringify(value)) {
      setText(incoming);
      setParseError(null);
      onError(null);
    }
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
