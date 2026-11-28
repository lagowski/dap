"use client";

import { useState } from "react";
import Link from "next/link";
import { FolderOpen, Users } from "lucide-react";
import { usePipelineUsage } from "@/hooks/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

/**
 * Toolbar control for a saved pipeline showing which projects bind it
 * (#697). The button carries the count; clicking opens a dialog that
 * lists each project and the workflow kind(s) it's bound as, linking
 * through to the project. Mirrors the agent detail "Used in" card.
 */
export function PipelineUsageDialog({ pipelineId }: { pipelineId: string }) {
  const [open, setOpen] = useState(false);
  const { data, isPending, isError } = usePipelineUsage(pipelineId);
  const count = data?.projects.length ?? 0;

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        title="Projects that use this pipeline"
      >
        <Users className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
        Used in {isPending ? "…" : count}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Used in projects</DialogTitle>
            <DialogDescription>
              Projects that bind this pipeline, and the workflow kind(s) they use it as.
            </DialogDescription>
          </DialogHeader>

          {isError ? (
            <p className="text-sm text-destructive">Failed to load usage.</p>
          ) : isPending ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : count === 0 ? (
            <p className="text-sm text-muted-foreground">
              Not used by any project yet.
            </p>
          ) : (
            <ul className="space-y-1.5 max-h-[60vh] overflow-y-auto">
              {data!.projects.map((proj) => (
                <li key={proj.id}>
                  <Link
                    href={`/projects/${proj.id}`}
                    className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 hover:bg-accent"
                    onClick={() => setOpen(false)}
                  >
                    <span className="flex items-center gap-2 min-w-0">
                      <FolderOpen
                        className="h-4 w-4 text-muted-foreground shrink-0"
                        aria-hidden="true"
                      />
                      <span className="truncate">{proj.name}</span>
                    </span>
                    <span className="flex flex-wrap gap-1 justify-end shrink-0">
                      {proj.kinds.map((kind) => (
                        <Badge key={kind} variant="secondary" className="font-mono text-xs">
                          {kind}
                        </Badge>
                      ))}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
