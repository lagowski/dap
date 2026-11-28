"use client";

/**
 * Node ``retry`` policy editor for the Inspector (#931).
 *
 * Edits the optional ``PipelineNode.retry`` block: how many retries, where
 * to re-enter, where to go once they're spent, which ``extensions`` key gets
 * the failure reason, and an optional short-circuit condition. The engine's
 * validator owns the rules (range, targets, loop-back); this only shapes the
 * block. Runtime routing lands in #932.
 */

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { RetryPolicy } from "@/lib/api/types";

import { ConditionBuilder } from "../condition-builder";

import { Field } from "./shared";

const END_SENTINEL = "__end__";
const DEFAULT_MAX_RETRIES = 3;
const SELECT_CLASS =
  "h-8 w-full rounded border border-input bg-background px-2 text-xs font-mono";


/** Starting policy: retry the first other node (or itself), end on failure. */
export function defaultRetryPolicy(nodeId: string, nodeIds: string[]): RetryPolicy {
  return {
    max_retries: DEFAULT_MAX_RETRIES,
    retry_to: nodeIds.find((id) => id !== nodeId) ?? nodeId,
    on_failure: END_SENTINEL,
    feedback_into: null,
    short_circuit_on: null,
  };
}


interface RetryEditorProps {
  nodeId: string;
  /** Every node id in the pipeline, used for the target pickers. */
  nodeIds: string[];
  retry: RetryPolicy | null;
  onChange: (retry: RetryPolicy | null) => void;
}


export function RetryEditor({ nodeId, nodeIds, retry, onChange }: RetryEditorProps) {
  if (retry === null) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">No retry policy — a failure ends the node.</p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange(defaultRetryPolicy(nodeId, nodeIds))}
        >
          <Plus className="h-3 w-3 mr-1" />
          Add retry
        </Button>
      </div>
    );
  }

  const fieldId = (name: string) => `retry-${nodeId}-${name}`;

  return (
    <div className="space-y-2 rounded-md border p-2 bg-muted/20">
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <label htmlFor={fieldId("max")} className="text-[11px] text-muted-foreground">
            Max retries
          </label>
          <Input
            id={fieldId("max")}
            type="number"
            min={1}
            max={20}
            value={retry.max_retries}
            onChange={(e) => onChange({ ...retry, max_retries: Number(e.target.value) })}
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1">
          <label htmlFor={fieldId("to")} className="text-[11px] text-muted-foreground">
            Retry to
          </label>
          <select
            id={fieldId("to")}
            value={retry.retry_to}
            onChange={(e) => onChange({ ...retry, retry_to: e.target.value })}
            className={SELECT_CLASS}
          >
            {nodeIds.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="space-y-1">
        <label htmlFor={fieldId("fail")} className="text-[11px] text-muted-foreground">
          On failure
        </label>
        <select
          id={fieldId("fail")}
          value={retry.on_failure}
          onChange={(e) => onChange({ ...retry, on_failure: e.target.value })}
          className={SELECT_CLASS}
        >
          {[...nodeIds, END_SENTINEL].map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-1">
        <label htmlFor={fieldId("feedback")} className="text-[11px] text-muted-foreground">
          Feedback into
        </label>
        <Input
          id={fieldId("feedback")}
          value={retry.feedback_into ?? ""}
          placeholder="extensions key, e.g. test_feedback"
          onChange={(e) => {
            const key = e.target.value.trim();
            onChange({ ...retry, feedback_into: key.length > 0 ? key : null });
          }}
          className="h-8 text-xs font-mono"
        />
      </div>

      <Field label="Short-circuit on">
        <ConditionBuilder
          condition={retry.short_circuit_on ?? null}
          onChange={(condition) => onChange({ ...retry, short_circuit_on: condition })}
          emptyHint="No short-circuit condition. Add one to mark an attempt as failed."
        />
      </Field>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => onChange(null)}
        className="text-destructive hover:text-destructive"
      >
        <Trash2 className="h-3 w-3 mr-1" />
        Remove retry
      </Button>
    </div>
  );
}
