"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";

import {
  useInstanceEnvVars,
  useUpsertInstanceEnvVar,
  useDeleteInstanceEnvVar,
} from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Admin editor for instance environment variables (#388 UI). Backed by the
 * masked-preview API — values are encrypted at rest and never read back. Lets
 * an admin set provider keys (ANTHROPIC_API_KEY…) and the assistant toggle
 * (ASSISTANT_PROVIDER) from the UI instead of editing the engine's .env.local.
 * DAP_*, POSTGRES_*, PYTHON* names are reserved (the engine rejects them).
 */
export function InstanceEnvVarsCard() {
  const list = useInstanceEnvVars();
  const upsert = useUpsertInstanceEnvVar();
  const remove = useDeleteInstanceEnvVar();
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");

  const canAdd = key.trim().length > 0 && value.length > 0 && !upsert.isPending;
  const add = () => {
    if (!canAdd) return;
    upsert.mutate(
      { key: key.trim(), value },
      {
        onSuccess: () => {
          setKey("");
          setValue("");
        },
      },
    );
  };

  const rows = list.data?.env_vars ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Instance environment variables</CardTitle>
        <CardDescription>
          Encrypted at rest; read by the engine and the config assistant (e.g.{" "}
          <span className="font-mono">ANTHROPIC_API_KEY</span>,{" "}
          <span className="font-mono">ASSISTANT_PROVIDER</span>). Values are write-only — only a
          masked preview is shown. <span className="font-mono">DAP_*</span>,{" "}
          <span className="font-mono">POSTGRES_*</span>, <span className="font-mono">PYTHON*</span>{" "}
          and a few system names are reserved.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {list.isError && (
          <p role="alert" className="text-sm text-destructive">
            {formatApiError(list.error)}
          </p>
        )}

        {rows.length > 0 ? (
          <table className="w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr>
                <th className="py-1 font-medium">Key</th>
                <th className="py-1 font-medium">Value</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key} className="border-t">
                  <td className="py-1.5 font-mono">{row.key}</td>
                  <td className="py-1.5 font-mono text-muted-foreground">{row.preview}</td>
                  <td className="py-1.5 text-right">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label={`Delete ${row.key}`}
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(row.key)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : !list.isPending ? (
          <p className="text-sm text-muted-foreground">No instance env vars set.</p>
        ) : null}

        <div className="flex flex-wrap items-end gap-2">
          <Input
            aria-label="Env var name"
            placeholder="KEY (e.g. ASSISTANT_PROVIDER)"
            className="font-mono"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <Input
            aria-label="Env var value"
            placeholder="value (e.g. claude-code)"
            className="font-mono"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <Button type="button" onClick={add} disabled={!canAdd}>
            {upsert.isPending ? "Saving…" : "Add / update"}
          </Button>
        </div>
        {upsert.isError && (
          <p role="alert" className="text-sm text-destructive">
            {formatApiError(upsert.error)}
          </p>
        )}
        {remove.isError && (
          <p role="alert" className="text-sm text-destructive">
            {formatApiError(remove.error)}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
