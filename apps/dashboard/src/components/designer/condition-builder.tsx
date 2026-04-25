"use client";

import { Trash2, Plus } from "lucide-react";
import type {
  ComparisonCondition,
  ComparisonOperator,
  EdgeCondition,
  LogicalCondition,
} from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const OPERATORS: ComparisonOperator[] = ["==", "!=", "<", "<=", ">", ">="];

const STATE_FIELDS = [
  "tests_passed",
  "tests_generated",
  "attempt",
  "max_attempts",
  "verification_status",
  "final_status",
  "selected_issue_ids",
  "modified_files",
] as const;

interface ConditionBuilderProps {
  condition: EdgeCondition | null;
  onChange: (condition: EdgeCondition | null) => void;
}

export function ConditionBuilder({ condition, onChange }: ConditionBuilderProps) {
  if (condition === null) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">
          Edge has no condition (always taken). Add one to make it conditional.
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({
                type: "comparison",
                field: "tests_passed",
                operator: "==",
                value: true,
              })
            }
          >
            <Plus className="h-3 w-3 mr-1" />
            Comparison
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({
                type: "and",
                children: [
                  { type: "comparison", field: "tests_passed", operator: "==", value: false },
                  { type: "comparison", field: "attempt", operator: "<", value: 3 },
                ],
              })
            }
          >
            <Plus className="h-3 w-3 mr-1" />
            AND/OR
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <ConditionNode condition={condition} onChange={onChange} depth={0} />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => onChange(null)}
        className="text-destructive hover:text-destructive"
      >
        <Trash2 className="h-3 w-3 mr-1" />
        Remove condition
      </Button>
    </div>
  );
}

interface ConditionNodeProps {
  condition: EdgeCondition;
  onChange: (condition: EdgeCondition) => void;
  depth: number;
}

function ConditionNode({ condition, onChange, depth }: ConditionNodeProps) {
  if (condition.type === "comparison") {
    return (
      <ComparisonRow condition={condition} onChange={onChange} indent={depth} />
    );
  }
  return <LogicalGroup condition={condition} onChange={onChange} depth={depth} />;
}

interface ComparisonRowProps {
  condition: ComparisonCondition;
  onChange: (condition: ComparisonCondition) => void;
  indent: number;
}

function ComparisonRow({ condition, onChange, indent }: ComparisonRowProps) {
  return (
    <div
      className="flex flex-wrap items-center gap-1.5 rounded-md border p-2 bg-muted/20"
      style={{ marginLeft: indent * 12 }}
    >
      <select
        value={condition.field}
        onChange={(e) => onChange({ ...condition, field: e.target.value })}
        className="h-8 rounded border border-input bg-background px-2 text-xs font-mono"
      >
        {STATE_FIELDS.map((f) => (
          <option key={f} value={f}>
            {f}
          </option>
        ))}
      </select>
      <select
        value={condition.operator}
        onChange={(e) =>
          onChange({ ...condition, operator: e.target.value as ComparisonOperator })
        }
        className="h-8 rounded border border-input bg-background px-2 text-xs"
      >
        {OPERATORS.map((op) => (
          <option key={op} value={op}>
            {op}
          </option>
        ))}
      </select>
      <ValueInput
        value={condition.value}
        onChange={(value) => onChange({ ...condition, value })}
      />
    </div>
  );
}

interface ValueInputProps {
  value: string | number | boolean | null;
  onChange: (value: string | number | boolean | null) => void;
}

function ValueInput({ value, onChange }: ValueInputProps) {
  const inferType = (): "boolean" | "number" | "string" | "null" => {
    if (typeof value === "boolean") return "boolean";
    if (typeof value === "number") return "number";
    if (value === null) return "null";
    return "string";
  };

  const type = inferType();
  return (
    <div className="flex items-center gap-1">
      <select
        value={type}
        onChange={(e) => {
          const t = e.target.value;
          if (t === "boolean") onChange(true);
          else if (t === "number") onChange(0);
          else if (t === "null") onChange(null);
          else onChange("");
        }}
        className="h-8 rounded border border-input bg-background px-2 text-xs text-muted-foreground"
      >
        <option value="boolean">bool</option>
        <option value="number">num</option>
        <option value="string">str</option>
        <option value="null">null</option>
      </select>
      {type === "boolean" && (
        <select
          value={String(value)}
          onChange={(e) => onChange(e.target.value === "true")}
          className="h-8 rounded border border-input bg-background px-2 text-xs"
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      )}
      {type === "number" && (
        <Input
          type="number"
          value={String(value ?? 0)}
          onChange={(e) => onChange(Number(e.target.value))}
          className="h-8 w-24 text-xs"
        />
      )}
      {type === "string" && (
        <Input
          type="text"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-32 text-xs"
        />
      )}
      {type === "null" && (
        <span className="text-xs text-muted-foreground italic">null</span>
      )}
    </div>
  );
}

interface LogicalGroupProps {
  condition: LogicalCondition;
  onChange: (condition: LogicalCondition) => void;
  depth: number;
}

function LogicalGroup({ condition, onChange, depth }: LogicalGroupProps) {
  return (
    <div
      className="rounded-md border bg-muted/20 p-2 space-y-2"
      style={{ marginLeft: depth * 12 }}
    >
      <div className="flex items-center gap-2">
        <Label className="text-xs">Operator</Label>
        <select
          value={condition.type}
          onChange={(e) =>
            onChange({ ...condition, type: e.target.value as "and" | "or" })
          }
          className="h-8 rounded border border-input bg-background px-2 text-xs uppercase font-bold"
        >
          <option value="and">AND</option>
          <option value="or">OR</option>
        </select>
      </div>
      <div className="space-y-2">
        {condition.children.map((child, idx) => (
          <div key={idx} className="flex items-start gap-1">
            <ConditionNode
              condition={child}
              onChange={(updated) => {
                const newChildren = [...condition.children];
                newChildren[idx] = updated;
                onChange({ ...condition, children: newChildren });
              }}
              depth={depth + 1}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => {
                const newChildren = condition.children.filter((_, i) => i !== idx);
                onChange({ ...condition, children: newChildren });
              }}
              className="h-7 w-7 shrink-0"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        ))}
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() =>
          onChange({
            ...condition,
            children: [
              ...condition.children,
              {
                type: "comparison",
                field: "tests_passed",
                operator: "==",
                value: true,
              },
            ],
          })
        }
      >
        <Plus className="h-3 w-3 mr-1" />
        Add child
      </Button>
    </div>
  );
}
