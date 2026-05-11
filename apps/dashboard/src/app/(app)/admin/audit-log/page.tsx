"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, FileClock, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuditEvents } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import type { AuditEvent } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

/**
 * Render event_data as compact JSON. Long blobs get truncated with a
 * tooltip carrying the full content. Null / empty payloads show "—".
 */
function formatEventData(data: AuditEvent["event_data"]): {
  short: string;
  full: string;
} {
  if (data === null || Object.keys(data).length === 0) {
    return { short: "—", full: "" };
  }
  const full = JSON.stringify(data);
  const short = full.length > 60 ? full.slice(0, 57) + "…" : full;
  return { short, full };
}

export default function AdminAuditLogPage() {
  const [eventType, setEventType] = useState("");
  const [userId, setUserId] = useState("");
  const [offset, setOffset] = useState(0);

  // Filters reset offset to 0 — paging from page 5 to a new
  // narrower filter would otherwise land on an empty page.
  function applyFilter(setter: (v: string) => void, value: string) {
    setter(value);
    setOffset(0);
  }

  const events = useAuditEvents({
    eventType: eventType.trim() || undefined,
    userId: userId.trim() || undefined,
    offset,
    limit: PAGE_SIZE,
  });

  const total = events.data?.total ?? 0;
  const hasNextPage = offset + PAGE_SIZE < total;
  const hasPrevPage = offset > 0;

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Audit log</h1>
        <p className="text-sm text-muted-foreground">
          Append-only record of security-relevant events. Filter by event type
          or actor; newest events appear first.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileClock className="h-5 w-5 text-muted-foreground" aria-hidden />
            Filters
          </CardTitle>
          <CardDescription>
            Exact-match on each field. Leave blank to show everything.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="event-type">Event type</Label>
            <Input
              id="event-type"
              placeholder="user.logged_in"
              value={eventType}
              onChange={(e) => applyFilter(setEventType, e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="user-id">User ID</Label>
            <Input
              id="user-id"
              placeholder="UUID"
              value={userId}
              onChange={(e) => applyFilter(setUserId, e.target.value)}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            {events.data
              ? `${total.toLocaleString()} ${total === 1 ? "event" : "events"}`
              : "Events"}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {events.isLoading && (
            <div className="flex items-center gap-2 px-6 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Loading events…
            </div>
          )}
          {events.isError && (
            <p className="px-6 py-8 text-sm text-destructive">
              {formatApiError(events.error)}
            </p>
          )}
          {events.data && events.data.items.length === 0 && (
            <p className="px-6 py-8 text-sm text-muted-foreground">
              No events match the current filters.
            </p>
          )}
          {events.data && events.data.items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
                    <th className="px-4 py-2 font-medium">When</th>
                    <th className="px-4 py-2 font-medium">Event</th>
                    <th className="px-4 py-2 font-medium">Actor</th>
                    <th className="px-4 py-2 font-medium">Data</th>
                  </tr>
                </thead>
                <tbody>
                  {events.data.items.map((event) => {
                    const { short, full } = formatEventData(event.event_data);
                    return (
                      <tr
                        key={event.id}
                        className="border-b last:border-b-0 hover:bg-accent/30"
                      >
                        <td className="px-4 py-3 align-middle text-xs whitespace-nowrap text-muted-foreground">
                          {formatTimestamp(event.created_at)}
                        </td>
                        <td className="px-4 py-3 align-middle">
                          <code className="text-xs font-mono">
                            {event.event_type}
                          </code>
                        </td>
                        <td className="px-4 py-3 align-middle text-xs">
                          {event.user_id ? (
                            <code
                              className="font-mono text-muted-foreground"
                              title={event.user_id}
                            >
                              {event.user_id.slice(0, 8)}…
                            </code>
                          ) : (
                            <span className="text-muted-foreground">system</span>
                          )}
                        </td>
                        <td
                          className={cn(
                            "px-4 py-3 align-middle text-xs font-mono text-muted-foreground",
                          )}
                          title={full}
                        >
                          {short}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
        {events.data && total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t px-6 py-3 text-xs text-muted-foreground">
            <span>
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of{" "}
              {total.toLocaleString()}
            </span>
            <div className="space-x-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={!hasPrevPage}
                aria-label="Previous page"
              >
                <ChevronLeft className="h-3 w-3" aria-hidden />
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={!hasNextPage}
                aria-label="Next page"
              >
                <ChevronRight className="h-3 w-3" aria-hidden />
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
