"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { usePipelinesList } from "@/hooks/api";
import {
  addBinding,
  removeBinding,
  updateBinding,
} from "@/components/projects/pipeline-bindings";

interface PipelineBindingsEditorProps {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}

export function PipelineBindingsEditor({
  value,
  onChange,
}: PipelineBindingsEditorProps) {
  const { data: pipelinesList } = usePipelinesList();
  const entries = Object.entries(value);

  return (
    <div className="space-y-2">
      {entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">No pipeline bindings yet.</p>
      ) : (
        <div className="space-y-1">
          {entries.map(([kind, pipelineId]) => (
            <div key={kind} className="flex items-center gap-2">
              <Input
                defaultValue={kind}
                onBlur={(e) =>
                  onChange(updateBinding(value, kind, e.target.value.trim(), pipelineId))
                }
                placeholder="kind"
                className="font-mono text-xs h-8 max-w-[14rem]"
                aria-label="binding kind"
              />
              <select
                value={pipelineId}
                onChange={(e) =>
                  onChange(updateBinding(value, kind, kind, e.target.value))
                }
                className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-xs font-mono shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                aria-label="pipeline"
              >
                <option value="">Select pipeline...</option>
                {pipelinesList?.items.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onChange(removeBinding(value, kind))}
                aria-label={`Remove ${kind}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange(addBinding(value))}
      >
        <Plus className="h-3.5 w-3.5 mr-1" />
        Add binding
      </Button>
    </div>
  );
}
