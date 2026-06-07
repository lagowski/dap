import { AlertTriangle } from "lucide-react";

/**
 * Side-effects warning + acknowledgement gate for dry-running a managed agent
 * (#739 slice 3). The dry-run sandbox only discards local file edits — for a
 * cortex python-func callable, "Run test" executes the real node: it can PATCH
 * a real GitHub issue, write to the cortex DB, and spend tokens. So we make the
 * user explicitly acknowledge before the run buttons unlock.
 */
export function ManagedDryRunWarning({
  checked,
  onCheckedChange,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="space-y-2 rounded-md border border-amber-300/60 bg-amber-50 p-3 text-sm dark:border-amber-700/50 dark:bg-amber-950/30">
      <div className="flex gap-2">
        <AlertTriangle
          className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400"
          aria-hidden
        />
        <div className="space-y-1">
          <p className="font-medium text-amber-900 dark:text-amber-200">
            This isn&apos;t a safe sandbox — it runs the real callable.
          </p>
          <p className="text-amber-800/90 dark:text-amber-300/80">
            Managed (Cortex) agents run a Python callable with real side effects. A dry-run can
            write to GitHub, the cortex database, and spend tokens — only local file edits are
            discarded. To genuinely test the workflow, run the pipeline against a throwaway issue
            instead.
          </p>
        </div>
      </div>
      <label className="flex items-center gap-2 pl-6 text-amber-900 dark:text-amber-200">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onCheckedChange(e.target.checked)}
          className="h-3.5 w-3.5"
        />
        I understand the side effects — run it anyway.
      </label>
    </div>
  );
}
