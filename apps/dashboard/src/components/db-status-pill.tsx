"use client";

import { Circle } from "lucide-react";
import { useHealth } from "@/hooks/api";
import { cn } from "@/lib/utils";

// Three states the operator cares about:
//   green  — DB reachable, dialect is postgresql
//   amber  — DB reachable, dialect is sqlite (single-user / local default)
//   red    — /health failed or db_reachable=false
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
 */
export function DbStatusPill() {
  const health = useHealth();

  let tone: Tone;
  let label: string;
  if (health.isPending) {
    tone = "amber";
    label = "Checking database…";
  } else if (health.isError || !health.data?.db_reachable) {
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
