"use client";

import { useId } from "react";
import { Sparkles } from "lucide-react";
import { Label } from "@/components/ui/label";
import {
  AGENT_TEMPLATES,
  AGENT_TEMPLATE_CATEGORIES,
  findAgentTemplate,
  type AgentTemplate,
} from "@/lib/agent-templates";

const SCRATCH_VALUE = "__scratch__";

interface TemplatePickerProps {
  value: string | null;
  onChange: (template: AgentTemplate | null) => void;
  disabled?: boolean;
}

/**
 * Native ``<select>`` with optgroup-per-category. Default value
 * ``"Start from scratch"`` keeps the empty-form path. Picking a
 * template hands the parent the full ``AgentTemplate`` so it can
 * rebuild the form's initial values.
 */
export function TemplatePicker({ value, onChange, disabled = false }: TemplatePickerProps) {
  const id = useId();
  const selected = value && findAgentTemplate(value);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="flex items-center gap-1.5">
        <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
        Start from template
      </Label>
      <select
        id={id}
        value={value ?? SCRATCH_VALUE}
        onChange={(e) => {
          const next = e.target.value;
          onChange(next === SCRATCH_VALUE ? null : findAgentTemplate(next) ?? null);
        }}
        disabled={disabled}
        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
      >
        <option value={SCRATCH_VALUE}>Start from scratch</option>
        {AGENT_TEMPLATE_CATEGORIES.map((cat) => {
          const inCategory = AGENT_TEMPLATES.filter((t) => t.category === cat);
          if (inCategory.length === 0) return null;
          return (
            <optgroup key={cat} label={cat}>
              {inCategory.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </optgroup>
          );
        })}
      </select>
      {selected ? (
        <p className="text-xs text-muted-foreground">{selected.description}</p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Pick a starter to pre-fill runtime, prompt, and contracts.
          You can edit any field before saving.
        </p>
      )}
    </div>
  );
}
