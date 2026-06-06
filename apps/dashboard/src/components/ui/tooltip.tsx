"use client";

import { cloneElement, isValidElement, useId } from "react";
import type { ReactElement, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Dependency-free hover/focus tooltip (no Radix). Wrap a single
 * interactive element; the label appears above it on hover or keyboard
 * focus. Works even when the trigger is ``disabled`` because the hover
 * lives on the wrapper, not the button.
 *
 * Accessibility: the tooltip text is linked to the wrapped element via
 * ``aria-describedby`` (injected with ``cloneElement``), so screen readers
 * announce it on focus. Still give icon-only triggers their own
 * ``aria-label`` for the accessible *name*.
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
  const id = useId();
  const trigger = isValidElement(children)
    ? cloneElement(
        children as ReactElement<{ "aria-describedby"?: string }>,
        { "aria-describedby": id },
      )
    : children;

  return (
    <span className={cn("relative inline-flex group/tt", className)}>
      {trigger}
      <span
        id={id}
        role="tooltip"
        className={cn(
          "pointer-events-none absolute left-1/2 z-50 -translate-x-1/2 whitespace-nowrap",
          "rounded-md border bg-foreground px-2 py-1 text-xs text-background shadow-md",
          // Hidden by default; short show-delay on hover so sweeping across
          // adjacent triggers doesn't flicker, instant hide on leave, and
          // immediate show on keyboard focus.
          "opacity-0 transition-opacity duration-100",
          "group-hover/tt:opacity-100 group-hover/tt:delay-300",
          "group-focus-within/tt:opacity-100",
          side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5",
        )}
      >
        {label}
      </span>
    </span>
  );
}
