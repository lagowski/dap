import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/** A spinning loader icon. */
export function Spinner({ className }: { className?: string }) {
  return (
    <Loader2
      className={cn("h-4 w-4 animate-spin text-muted-foreground", className)}
      aria-hidden="true"
    />
  );
}

/**
 * Inline "spinner + label" loading state for pages and sections — replaces
 * bare "Loading…" text so something actually moves while data loads.
 */
export function LoadingState({
  label = "Loading…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-center gap-2 text-sm text-muted-foreground",
        className,
      )}
    >
      <Spinner />
      <span>{label}</span>
    </div>
  );
}
