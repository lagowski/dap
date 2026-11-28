"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { EnvVarValidationResult } from "@/lib/api/types";

interface EnvVarsEditorProps {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  validationResults?: Record<string, EnvVarValidationResult>;
}

export function EnvVarsEditor({
  value,
  onChange,
  validationResults = {},
}: EnvVarsEditorProps) {
  const entries = Object.entries(value);

  const update = (oldKey: string, nextKey: string, nextValue: string) => {
    const next: Record<string, string> = {};
    for (const [k, v] of Object.entries(value)) {
      if (k === oldKey) {
        if (nextKey.length > 0) next[nextKey] = nextValue;
      } else {
        next[k] = v;
      }
    }
    onChange(next);
  };

  const remove = (key: string) => {
    const { [key]: _removed, ...rest } = value;
    onChange(rest);
  };

  const add = () => {
    let candidate = "VAR_NAME";
    let n = 1;
    while (candidate in value) {
      candidate = `VAR_NAME_${n++}`;
    }
    onChange({ ...value, [candidate]: "" });
  };

  return (
    <div className="space-y-2">
      {entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">No project env vars yet.</p>
      ) : (
        <div className="space-y-1">
          {entries.map(([key, val]) => {
            const vr = validationResults[key];
            const hasError = vr?.is_token && !vr.valid;
            return (
              <div key={key}>
                <div className="flex items-center gap-2">
                  <Input
                    defaultValue={key}
                    onBlur={(e) => update(key, e.target.value, val)}
                    placeholder="KEY"
                    className="font-mono text-xs h-8 max-w-[14rem]"
                    aria-label="env var name"
                  />
                  <Input
                    value={val}
                    onChange={(e) => update(key, key, e.target.value)}
                    placeholder="value"
                    className="font-mono text-xs h-8"
                    aria-label="env var value"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(key)}
                    aria-label={`Remove ${key}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {hasError && (
                  <p className="text-xs text-destructive ml-1 mt-0.5">
                    {vr.error ?? "Invalid token"}
                  </p>
                )}
                {vr?.is_token && vr.valid && vr.login && (
                  <p className="text-xs text-green-600 ml-1 mt-0.5">
                    Authenticated as {vr.login}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
      <Button type="button" variant="outline" size="sm" onClick={add}>
        <Plus className="h-3.5 w-3.5 mr-1" />
        Add env var
      </Button>
    </div>
  );
}
