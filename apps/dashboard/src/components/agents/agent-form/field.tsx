/**
 * Labelled form field wrapper (audit D1 split).
 *
 * Renders Label + child input + an optional inline error message.
 * Used everywhere in the agent form for visual + ARIA consistency.
 */

import { Label } from "@/components/ui/label";


interface FieldProps {
  label: string;
  error?: string;
  children: React.ReactNode;
}


export function Field({ label, error, children }: FieldProps) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
