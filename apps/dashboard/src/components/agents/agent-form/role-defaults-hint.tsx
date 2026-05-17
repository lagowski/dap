/**
 * Per-role recommendation hint shown above each schema picker
 * (audit D1 split).
 *
 * Surfaces what fields a conventional ``{role}`` agent typically
 * picks (from ROLE_DEFAULT_INPUT_SCHEMA / ROLE_DEFAULT_OUTPUT_SCHEMA)
 * plus a one-click "Use role defaults" button that pre-fills the
 * picker with exactly those fields. Hidden for roles without a
 * recommendation (e.g. ``post_check``, custom roles) — better than
 * showing an empty "Typical for X: ___" with nothing to fill it.
 */

import { Button } from "@/components/ui/button";


interface RoleDefaultsHintProps {
  role: string;
  recommended: readonly string[] | undefined;
  current: readonly string[];
  onUseDefaults: (next: string[]) => void;
  subjectLabel: "inputs" | "outputs";
}


export function RoleDefaultsHint({
  role,
  recommended,
  current,
  onUseDefaults,
  subjectLabel,
}: RoleDefaultsHintProps) {
  if (!recommended || recommended.length === 0) {
    return null;
  }
  const matches =
    current.length === recommended.length &&
    recommended.every((f) => current.includes(f));
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed bg-muted/20 px-3 py-2 text-xs">
      <span className="text-muted-foreground">
        Typical {subjectLabel} for{" "}
        <span className="font-mono">{role}</span>:
      </span>
      <span className="font-mono">{recommended.join(", ")}</span>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="ml-auto h-7 text-xs"
        onClick={() => onUseDefaults([...recommended])}
        disabled={matches}
        title={
          matches
            ? "Picker already matches the role default"
            : "Replace the current picker selection with the role default"
        }
      >
        Use role defaults
      </Button>
    </div>
  );
}
