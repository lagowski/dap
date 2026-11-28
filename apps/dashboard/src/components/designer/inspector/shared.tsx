"use client";

/**
 * Shared layout primitive for the Inspector sidebar (audit D1 split).
 *
 * ``Field`` is a tiny label-over-content stack used by both the
 * Node and Edge panels. Lives in its own file so neither panel has
 * to reach across into the other to use it.
 */

import { Label } from "@/components/ui/label";


export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}
