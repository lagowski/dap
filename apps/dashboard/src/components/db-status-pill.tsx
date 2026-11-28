"use client";

import { Circle } from "lucide-react";
import { useHealth } from "@/hooks/api";
import { cn } from "@/lib/utils";

// Four states the operator cares about (#622 — split engine-busy from db-down):
//   green  — DB reachable, dialect is postgresql
//   amber  — DB reachable, dialect is sqlite (single-user / local default)
//   amber  — engine /health hung or returned an error (busy worker, network,
//            startup) — DB status is genuinely unknown, do NOT claim DB is down
//   red    — engine responded AND explicitly reported db_reachable=false
type Tone = "green" | "amber" | "red";

const STYLES: Record<Tone, string> = {
  green: "bg-emerald-50 text-emerald-700 border-emerald-200",
  amber: "bg-amber-50 text-amber-800 border-amber-200",
  red: "bg-destructive/10 text-destructive border-destructive/30",
};

const DOT: Record<Tone, string> = {
  green: "fill-emerald-500 text-emerald-500",
  amber: "fill-amber-500 text-amber-500",
  red: "fill-destructive text-destructive",
};

/**
 * Small status pill that surfaces the engine's database connection
 * state. Used on the login + signup pages so a new operator can
 * distinguish "wrong credentials" from "the engine can't reach its
 * database". Endpoint: GET /health → { db_dialect, db_reachable }.
 *
 * #622 — when ``health.isError`` (typically a /health timeout or 5xx
 * during a busy in-process node — see #621 single-worker blocking),
 * we explicitly show "Engine busy or unavailable" in amber rather
 * than misreporting it as a red "Database unreachable". An operator
 * panicking at a fake DB failure and restarting Postgres is the
 * exact harm we're trying to prevent. Red is reserved for the case
 * where /health responded *successfully* and the engine explicitly
 * told us its DB connection failed.
 */
export function DbStatusPill() {
  const health = useHealth();

  let tone: Tone;
  let label: string;
  if (health.isPending) {
    tone = "amber";
    label = "Checking database…";
  } else if (health.isError) {
    // /health itself failed — engine busy / unreachable / hung. We
    // genuinely do NOT know the DB status. Don't lie that DB is down.
    tone = "amber";
    label = "Engine busy or unavailable";
  } else if (!health.data?.db_reachable) {
    // Engine responded AND explicitly says DB is unreachable.
    tone = "red";
    label = "Database unreachable";
  } else if (health.data.db_dialect === "postgresql") {
    tone = "green";
    label = "Connected to PostgreSQL";
  } else {
    tone = "amber";
    label = "Using local SQLite";
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5",
        "text-[11px] font-medium",
        STYLES[tone],
      )}
    >
      <Circle className={cn("h-2 w-2", DOT[tone])} aria-hidden />
      {label}
    </div>
  );
}
