"use client";

import Link from "next/link";
import { ExternalLink } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import type { AuditEvent } from "@/lib/api/types";

/**
 * Known resource-id keys that can appear in ``event_data`` and the page
 * they link to. Lets an admin jump from an audit row straight to the
 * agent / project / pipeline / run the event is about (#708).
 */
const RESOURCE_LINKS: Record<string, (id: string) => string> = {
  agent_id: (id) => `/agents/${id}`,
  project_id: (id) => `/projects/${id}`,
  pipeline_id: (id) => `/pipelines/${id}/edit`,
  run_id: (id) => `/runs/${id}`,
};

function resourceLinks(data: AuditEvent["event_data"]): { label: string; href: string }[] {
  if (!data) return [];
  const out: { label: string; href: string }[] = [];
  for (const [key, build] of Object.entries(RESOURCE_LINKS)) {
    const value = (data as Record<string, unknown>)[key];
    if (typeof value === "string" && value.length > 0) {
      out.push({ label: `${key}: ${value}`, href: build(value) });
    }
  }
  return out;
}

export function AuditEventDetailDialog({
  event,
  onClose,
}: {
  event: AuditEvent | null;
  onClose: () => void;
}) {
  const links = resourceLinks(event?.event_data ?? null);

  return (
    <Dialog open={event != null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-mono text-base">
            {event?.event_type}
          </DialogTitle>
        </DialogHeader>

        {event && (
          <div className="space-y-4 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
              <dt className="text-muted-foreground">When</dt>
              <dd suppressHydrationWarning>
                {new Date(event.created_at).toLocaleString()}
              </dd>
              <dt className="text-muted-foreground">Actor</dt>
              <dd className="font-mono break-all">
                {event.user_id ?? (
                  <span className="text-muted-foreground">system</span>
                )}
              </dd>
              <dt className="text-muted-foreground">Event ID</dt>
              <dd className="font-mono break-all text-xs text-muted-foreground">
                {event.id}
              </dd>
            </dl>

            {links.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {links.map((l) => (
                  <Link key={l.href} href={l.href} onClick={onClose}>
                    <Badge variant="secondary" className="gap-1 font-mono text-xs">
                      {l.label}
                      <ExternalLink className="h-3 w-3" aria-hidden />
                    </Badge>
                  </Link>
                ))}
              </div>
            )}

            <div>
              <div className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">
                Data
              </div>
              {event.event_data && Object.keys(event.event_data).length > 0 ? (
                <pre className="max-h-[50vh] overflow-auto rounded bg-muted p-3 text-xs">
                  {JSON.stringify(event.event_data, null, 2)}
                </pre>
              ) : (
                <p className="text-xs text-muted-foreground">No data payload.</p>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
