"use client";

import { ChevronRight, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  AGENT_TEMPLATES,
  AGENT_TEMPLATE_CATEGORIES,
  type AgentTemplate,
  type AgentTemplateCategory,
} from "@/lib/agent-templates";

interface TemplatePickerProps {
  onSelect: (template: AgentTemplate | null) => void;
  disabled?: boolean;
}

/**
 * Step-1 chooser: a grouped list of starting points. Picking a row (or
 * "Start from scratch") is a navigation action — the parent advances to
 * the form. There's no persistent "selected" state here: choosing moves
 * you forward, and "Back to templates" brings you back.
 */
export function TemplatePicker({ onSelect, disabled = false }: TemplatePickerProps) {
  const groups: { group: AgentTemplateCategory; templates: AgentTemplate[] }[] =
    AGENT_TEMPLATE_CATEGORIES.map((cat) => ({
      group: cat,
      templates: AGENT_TEMPLATES.filter((t) => t.category === cat),
    })).filter((g) => g.templates.length > 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1.5">
        <Sparkles className="h-4 w-4" aria-hidden="true" />
        <h2 className="text-base font-medium">How do you want to start?</h2>
      </div>

      <Row
        title="Start from scratch"
        description="Empty form with the default Jinja prompt skeleton — wire everything yourself."
        onClick={() => onSelect(null)}
        disabled={disabled}
      />

      {groups.map((group) => (
        <div key={group.group} className="space-y-1.5">
          <h3 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {group.group}
          </h3>
          <div className="space-y-1.5">
            {group.templates.map((t) => (
              <Row
                key={t.id}
                title={t.name}
                description={t.description}
                tags={tagsFor(t)}
                onClick={() => onSelect(t)}
                disabled={disabled}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function tagsFor(t: AgentTemplate): string[] {
  const modelId = t.runtime_config["model_id"];
  return [
    t.runtime_id,
    t.role,
    ...(typeof modelId === "string" ? [modelId] : []),
  ];
}

function Row({
  title,
  description,
  tags,
  onClick,
  disabled,
}: {
  title: string;
  description: string;
  tags?: string[];
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group/row flex w-full items-center gap-3 rounded-md border border-input bg-card px-3 py-2.5 text-left transition-colors hover:border-primary/40 hover:bg-accent/30 disabled:cursor-not-allowed disabled:opacity-60"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium leading-tight">{title}</span>
          {tags?.map((tag) => (
            <Badge
              key={tag}
              variant="secondary"
              className="font-mono text-[10px]"
            >
              {tag}
            </Badge>
          ))}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      </div>
      <ChevronRight
        className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-hover/row:translate-x-0.5"
        aria-hidden="true"
      />
    </button>
  );
}
