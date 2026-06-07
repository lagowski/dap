"use client";

/**
 * "Hide managed (Cortex) agents" list toggle (#739 slice 2). Renders nothing
 * when there are no managed agents to hide, so the agents list stays clean for
 * users who don't run Cortex.
 */
export function ManagedAgentsFilterToggle({
  count,
  checked,
  onCheckedChange,
}: {
  count: number;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  if (count === 0) return null;
  return (
    <label className="flex items-center gap-2 text-sm text-muted-foreground">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onCheckedChange(e.target.checked)}
        className="h-3.5 w-3.5"
      />
      Hide {count} managed (Cortex) agent{count === 1 ? "" : "s"}
    </label>
  );
}
