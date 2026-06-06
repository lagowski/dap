"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Dependency-free hover/focus tooltip (no Radix). Wrap a single
 * interactive element; the label appears above it on hover or keyboard
 * focus. Works even when the trigger is ``disabled`` because the hover
 * lives on the wrapper, not the button.
 *
 * This tooltip is presentational — give the wrapped control a matching
 * ``aria-label`` so screen readers still announce it.
 */
export function Tooltip({
  label,
  children,
  side = "top",
  className,
}: {
  label: string;
  children: ReactNode;
  side?: "top" | "bottom";
  className?: string;
}) {
  return (
    <span className={cn("relative inline-flex group/tt", className)}>
      {children}
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute left-1/2 z-50 -translate-x-1/2 whitespace-nowrap",
          "rounded-md border bg-foreground px-2 py-1 text-xs text-background shadow-md",
          "opacity-0 transition-opacity duration-100",
          "group-hover/tt:opacity-100 group-focus-within/tt:opacity-100",
          side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5",
        )}
      >
        {label}
      </span>
    </span>
  );
}
