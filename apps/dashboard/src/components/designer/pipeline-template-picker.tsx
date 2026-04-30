"use client";

import { Sparkles, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  PIPELINE_TEMPLATE_CATEGORIES,
  PIPELINE_TEMPLATES,
  type PipelineTemplate,
  type PipelineTemplateCategory,
} from "@/lib/pipeline-templates";

interface PipelineTemplatePickerProps {
  /**
   * Fired when the user clicks "Use this template" on a card. Parent
   * is responsible for the actual import + navigation. Awaiting the
   * promise lets the parent disable the picker while the request is
   * in flight (no double-imports on rage clicks).
   */
  onUseTemplate: (template: PipelineTemplate) => Promise<void> | void;
  /** Disable every card while an import is in flight. */
  disabled?: boolean;
  /** Surfaces the "currently importing" state on the matching card. */
  pendingTemplateId?: string | null;
}

/**
 * Card grid above the empty Designer on ``/pipelines/new``. Each
 * template ships with its own bundle (pipeline + agents); clicking
 * "Use this template" runs ``POST /pipelines/import`` and navigates
 * to ``/pipelines/{id}/edit`` for further customisation. No
 * "Start from scratch" card here — the empty Designer below is the
 * scratch path; the picker is purely an alternative bootstrap.
 */
export function PipelineTemplatePicker({
  onUseTemplate,
  disabled = false,
  pendingTemplateId = null,
}: PipelineTemplatePickerProps) {
  const groups: { group: PipelineTemplateCategory; templates: PipelineTemplate[] }[] =
    PIPELINE_TEMPLATE_CATEGORIES.map((cat) => ({
      group: cat,
      templates: PIPELINE_TEMPLATES.filter((t) => t.category === cat),
    })).filter((g) => g.templates.length > 0);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
        <h3 className="text-sm font-medium">Start from template</h3>
        <span className="text-xs text-muted-foreground">
          — drops a working pipeline + its agents into the engine in one click.
          You can edit everything afterwards on the pipeline&apos;s edit page.
        </span>
      </div>

      {groups.map((group) => (
        <div key={group.group} className="space-y-1.5">
          <h4 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {group.group}
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {group.templates.map((t) => (
              <PipelineTemplateCard
                key={t.id}
                template={t}
                onUse={() => onUseTemplate(t)}
                disabled={disabled}
                pending={pendingTemplateId === t.id}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function PipelineTemplateCard({
  template,
  onUse,
  disabled,
  pending,
}: {
  template: PipelineTemplate;
  onUse: () => void;
  disabled: boolean;
  pending: boolean;
}) {
  const nodeCount = template.bundle.pipeline.nodes.length;
  const agentCount = Object.keys(template.bundle.bundled_agents ?? {}).length;
  const runtimes = new Set(
    Object.values(template.bundle.bundled_agents ?? {}).map((a) => a.runtime_id),
  );
  return (
    <div
      className={cn(
        "h-full flex flex-col gap-2 rounded-md border bg-card p-3 transition-colors",
        "border-input",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-medium leading-tight">{template.name}</div>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <Badge variant="secondary" className="font-mono text-[10px]">
          {nodeCount} node{nodeCount === 1 ? "" : "s"}
        </Badge>
        <Badge variant="secondary" className="font-mono text-[10px]">
          {agentCount} agent{agentCount === 1 ? "" : "s"}
        </Badge>
        {Array.from(runtimes).map((runtime) => (
          <Badge key={runtime} variant="outline" className="font-mono text-[10px]">
            {runtime}
          </Badge>
        ))}
      </div>
      <p className="text-xs text-muted-foreground flex-1">{template.description}</p>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={onUse}
        disabled={disabled || pending}
        className="self-start"
      >
        <Wand2 className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
        {pending ? "Importing…" : "Use this template"}
      </Button>
    </div>
  );
}
