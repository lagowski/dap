"use client";

import { ChevronRight, Loader2, Sparkles, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  PIPELINE_TEMPLATE_CATEGORIES,
  PIPELINE_TEMPLATES,
  type PipelineTemplate,
  type PipelineTemplateCategory,
} from "@/lib/pipeline-templates";

interface PipelineTemplatePickerProps {
  /**
   * Fired when the user picks a template row. Parent is responsible for
   * the import + navigation. Awaiting the promise lets the parent disable
   * the list while the request is in flight (no double-imports).
   */
  onUseTemplate: (template: PipelineTemplate) => Promise<void> | void;
  /** Fired when the user picks "Start from a blank canvas". */
  onStartScratch: () => void;
  /** Disable every row while an import is in flight. */
  disabled?: boolean;
  /** Surfaces the "currently importing" state on the matching row. */
  pendingTemplateId?: string | null;
}

/**
 * Step-1 chooser for ``/pipelines/new``: a grouped list of starting
 * points. "Start from a blank canvas" advances to the empty Designer;
 * picking a template runs ``POST /pipelines/import`` and navigates to the
 * new pipeline's edit page. Choosing is a navigation action — there's no
 * persistent selection.
 */
export function PipelineTemplatePicker({
  onUseTemplate,
  onStartScratch,
  disabled = false,
  pendingTemplateId = null,
}: PipelineTemplatePickerProps) {
  const groups: {
    group: PipelineTemplateCategory;
    templates: PipelineTemplate[];
  }[] = PIPELINE_TEMPLATE_CATEGORIES.map((cat) => ({
    group: cat,
    templates: PIPELINE_TEMPLATES.filter((t) => t.category === cat),
  })).filter((g) => g.templates.length > 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1.5">
        <Sparkles className="h-4 w-4" aria-hidden="true" />
        <h2 className="text-base font-medium">How do you want to start?</h2>
      </div>

      <Row
        title="Start from a blank canvas"
        description="An empty Designer — add nodes and wire the pipeline yourself."
        icon={<Plus className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
        onClick={onStartScratch}
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
                onClick={() => onUseTemplate(t)}
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

function tagsFor(t: PipelineTemplate): string[] {
  const nodeCount = t.bundle.pipeline.nodes.length;
  const agentCount = Object.keys(t.bundle.bundled_agents ?? {}).length;
  const runtimes = Array.from(
    new Set(
      Object.values(t.bundle.bundled_agents ?? {}).map((a) => a.runtime_id),
    ),
  );
  return [
    `${nodeCount} node${nodeCount === 1 ? "" : "s"}`,
    `${agentCount} agent${agentCount === 1 ? "" : "s"}`,
    ...runtimes,
  ];
}

function Row({
  title,
  description,
  tags,
  icon,
  onClick,
  disabled,
  pending = false,
}: {
  title: string;
  description: string;
  tags?: string[];
  icon?: React.ReactNode;
  onClick: () => void;
  disabled: boolean;
  pending?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group/row flex w-full items-center gap-3 rounded-md border border-input bg-card px-3 py-2.5 text-left transition-colors hover:border-primary/40 hover:bg-accent/30 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {icon}
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
        <p className="mt-1 text-xs text-muted-foreground">
          {pending ? "Importing…" : description}
        </p>
      </div>
      {pending ? (
        <Loader2
          className="h-4 w-4 shrink-0 animate-spin text-muted-foreground"
          aria-hidden="true"
        />
      ) : (
        <ChevronRight
          className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-hover/row:translate-x-0.5"
          aria-hidden="true"
        />
      )}
    </button>
  );
}
