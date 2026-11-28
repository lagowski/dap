"use client";

/**
 * GitHub issue picker shown when triggering the ``cortex`` workflow
 * on a project that has ``repo_url`` set. Selecting an issue feeds
 * its number / title / url / body into the run's ``initial_state``
 * so the cortex pipeline knows what to work on (#371).
 *
 * Extracted from ``workflow-cards.tsx`` during the D1 audit split.
 */

import { AlertCircle, GitBranch, X } from "lucide-react";

import { useProjectIssues } from "@/hooks/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { Project } from "@/lib/api/types";


interface IssuePickerProps {
  project: Project;
  onSelect: (number: number, title: string, url: string, body: string) => void;
  onCancel: () => void;
}


export function IssuePicker({ project, onSelect, onCancel }: IssuePickerProps) {
  const { data: issues, isPending, isError } = useProjectIssues(project.id);

  return (
    <Card className="border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30">
      <CardContent className="pt-3 pb-3 space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold flex items-center gap-1">
            <GitBranch className="h-3.5 w-3.5" />
            Select an issue to implement
          </p>
          <Button type="button" variant="ghost" size="sm" className="h-6 px-1" onClick={onCancel}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>

        {isPending && (
          <p className="text-xs text-muted-foreground">Loading issues…</p>
        )}
        {isError && (
          <p className="text-xs text-destructive flex items-center gap-1">
            <AlertCircle className="h-3.5 w-3.5" />
            Could not fetch issues — check repo_url and GitHub token
          </p>
        )}
        {issues && issues.length === 0 && (
          <p className="text-xs text-muted-foreground italic">No open issues found.</p>
        )}
        {issues && issues.length > 0 && (
          <ul className="space-y-1 max-h-64 overflow-y-auto">
            {issues.map((issue) => (
              <li key={issue.number}>
                <button
                  type="button"
                  className="w-full text-left rounded px-2 py-1.5 hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors"
                  onClick={() => onSelect(issue.number, issue.title, issue.url, issue.body)}
                >
                  <div className="flex items-start gap-2">
                    <span className="text-xs font-mono text-muted-foreground shrink-0 mt-0.5">
                      #{issue.number}
                    </span>
                    <div className="min-w-0">
                      <p className="text-xs font-medium truncate">{issue.title}</p>
                      {issue.labels.length > 0 && (
                        <div className="flex gap-1 mt-0.5 flex-wrap">
                          {issue.labels.map((l) => (
                            <span key={l} className="text-[10px] bg-blue-200 dark:bg-blue-800 rounded px-1">
                              {l}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
