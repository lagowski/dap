"use client";

/**
 * Editor for ``kind: "kv"`` runtime-config fields — a dict of
 * string→string pairs rendered as an array of key/value input rows
 * (audit D1 split).
 *
 * Why an internal array-of-rows instead of mirroring the parent
 * dict directly: a user typing into the KEY input transiently
 * produces an empty key, which would collide with any other empty-
 * key row + lose the row entirely the moment we round-trip through
 * a ``Record``. Keeping rows separate lets the user edit a key
 * incrementally; we surface duplicate-key warnings inline rather
 * than silently overwriting.
 */

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";


interface KeyValueRow {
  key: string;
  value: string;
}


export function KeyValueEditor({
  id,
  value,
  onChange,
}: {
  id: string;
  value: Record<string, string>;
  onChange: (next: Record<string, string> | undefined) => void;
}) {
  const [rows, setRows] = useState<KeyValueRow[]>(() =>
    Object.entries(value).map(([k, v]) => ({ key: k, value: v })),
  );

  // Sync from outside when `value` is replaced (e.g. runtime change
  // reset). We compare the structural signature of our local rows
  // against the incoming ``value`` to decide whether the parent's
  // payload genuinely changed — without this the effect would
  // mirror-back on every keystroke. The ``rowsRef`` lets us read
  // the latest rows without depending on them (which would loop the
  // effect every render).
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  useEffect(() => {
    const externalEntries = Object.entries(value);
    const localAsObject = rowsToObject(rowsRef.current);
    if (JSON.stringify(localAsObject) !== JSON.stringify(value)) {
      setRows(externalEntries.map(([k, v]) => ({ key: k, value: v })));
    }
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
