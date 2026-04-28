"use client";

import { Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  AGENT_TEMPLATES,
  AGENT_TEMPLATE_CATEGORIES,
  type AgentTemplate,
  type AgentTemplateCategory,
} from "@/lib/agent-templates";

interface TemplatePickerProps {
  value: string | null;
  onChange: (template: AgentTemplate | null) => void;
  disabled?: boolean;
}

/**
 * Card-grid picker — descriptions are visible upfront for every
 * starter so the user can compare runtime / cost / capability
 * trade-offs at a glance instead of cycling through a dropdown.
 *
 * Cards are buttons with ``aria-pressed`` to communicate the toggle
 * state to assistive tech. The "Start from scratch" card sits at the
 * top of the list so the empty-form path is one click away.
 */
export function TemplatePicker({ value, onChange, disabled = false }: TemplatePickerProps) {
  const groups: { group: AgentTemplateCategory; templates: AgentTemplate[] }[] =
    AGENT_TEMPLATE_CATEGORIES.map((cat) => ({
      group: cat,
      templates: AGENT_TEMPLATES.filter((t) => t.category === cat),
    })).filter((g) => g.templates.length > 0);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
        <h3 className="text-sm font-medium">Start from template</h3>
        <span className="text-xs text-muted-foreground">
          — pre-fills runtime, prompt, contracts. Edit anything before saving.
        </span>
      </div>

      <ScratchCard
        active={value === null}
        onClick={() => onChange(null)}
        disabled={disabled}
      />

      {groups.map((group) => (
        <div key={group.group} className="space-y-1.5">
          <h4 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {group.group}
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {group.templates.map((t) => (
              <TemplateCard
                key={t.id}
                template={t}
                active={value === t.id}
                onClick={() => onChange(t)}
                disabled={disabled}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ScratchCard({
  active,
  onClick,
  disabled,
}: {
  active: boolean;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      disabled={disabled}
      className={cn(
        "w-full text-left rounded-md border bg-card p-3 transition-colors",
        "hover:bg-accent/30 disabled:cursor-not-allowed disabled:opacity-60",
        active
          ? "border-primary ring-1 ring-primary"
          : "border-input",
      )}
    >
      <div className="text-sm font-medium">Start from scratch</div>
      <div className="text-xs text-muted-foreground">
        Empty form with the default Jinja prompt skeleton. Pick this if you
        want to wire everything yourself.
      </div>
    </button>
  );
}

function TemplateCard({
  template,
  active,
  onClick,
  disabled,
}: {
  template: AgentTemplate;
  active: boolean;
  onClick: () => void;
  disabled: boolean;
}) {
  const modelId = template.runtime_config["model_id"];
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      disabled={disabled}
      className={cn(
        "h-full text-left rounded-md border bg-card p-3 transition-colors",
        "hover:bg-accent/30 disabled:cursor-not-allowed disabled:opacity-60",
        active
          ? "border-primary ring-1 ring-primary"
          : "border-input",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-medium leading-tight">{template.name}</div>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        <Badge variant="secondary" className="font-mono text-[10px]">
          {template.runtime_id}
        </Badge>
        <Badge variant="outline" className="font-mono text-[10px]">
          {template.role}
        </Badge>
        {typeof modelId === "string" ? (
          <Badge variant="outline" className="font-mono text-[10px]">
            {modelId}
          </Badge>
        ) : null}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{template.description}</p>
    </button>
  );
}
