"use client";

/**
 * Edge selection panel for the Inspector (audit D1 split).
 *
 * Shows the edge's source/target IDs, declared field flow
 * annotation, optional label, transition condition, and a delete
 * control. The condition editor is its own component
 * (``ConditionBuilder``) so this file stays focused on the panel
 * shell.
 */

import { Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { EdgeCondition } from "@/lib/api/types";
import type { EdgeAnnotation } from "@/lib/edge-annotations";

import { ConditionBuilder } from "../condition-builder";
import type { DesignerEdge } from "../types";

import { Field } from "./shared";


interface EdgePanelProps {
  edge: DesignerEdge;
  annotation: EdgeAnnotation;
  onUpdateCondition: (condition: EdgeCondition | null) => void;
  onUpdateLabel: (label: string) => void;
  onDelete: (edgeId: string) => void;
}


export function EdgePanel({
  edge,
  annotation,
  onUpdateCondition,
  onUpdateLabel,
  onDelete,
}: EdgePanelProps) {
  return (
    <div className="space-y-3">
      <Field label="Edge ID">
        <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
          {edge.id}
        </code>
      </Field>

      <div className="grid grid-cols-2 gap-2">
        <Field label="Source">
          <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
            {edge.source}
          </code>
        </Field>
        <Field label="Target">
          <code className="text-xs font-mono bg-muted px-2 py-1 rounded block">
            {edge.target}
          </code>
        </Field>
      </div>

      <Field label="Fields flowing through">
        {annotation.warning ? (
          <p className="text-xs text-destructive">
            Downstream node declares inputs but none match the upstream&apos;s
            outputs. Either widen the source&apos;s output_schema or narrow
            the target&apos;s input_schema.
          </p>
        ) : annotation.unknown ? (
          <p className="text-xs italic text-muted-foreground">
            Agent lookup failed — the agents list may still be loading,
            or one of the referenced agents has been archived/deleted.
          </p>
        ) : annotation.fields.length === 0 ? (
          <p className="text-xs italic text-muted-foreground">
            No declared field flow — at least one endpoint is in legacy
            mode (empty schema).
          </p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {annotation.fields.map((f) => (
              <Badge key={f} variant="outline" className="font-mono text-[10px]">
                {f}
              </Badge>
            ))}
          </div>
        )}
      </Field>

      <Field label="Label (optional)">
        <Input
          value={edge.label ?? ""}
          onChange={(e) => onUpdateLabel(e.target.value)}
          placeholder="e.g. on success"
          className="h-8 text-xs"
        />
      </Field>

      <Field label="Condition">
        <ConditionBuilder
          condition={edge.condition ?? null}
          onChange={onUpdateCondition}
        />
      </Field>

      <div className="pt-2 border-t">
        <Button
          type="button"
          variant="destructive"
          size="sm"
          onClick={() => onDelete(edge.id)}
          className="w-full"
        >
          <Trash2 className="h-3 w-3 mr-1" />
          Delete edge
        </Button>
      </div>
    </div>
  );
}
