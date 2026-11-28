"use client";

/**
 * Engine info card (audit D1 split). Read-only snapshot of the
 * engine's runtime metadata (version, recursion cap, DB paths)
 * — operators check this when troubleshooting "which DB am I
 * actually pointing at".
 */

import { Card, CardContent } from "@/components/ui/card";
import type { EngineInfo } from "@/lib/api/types";

import { Metric } from "./shared";


export function EngineSection({ engine }: { engine: EngineInfo }) {
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">Engine</h2>
      <Card>
        <CardContent className="pt-6 grid grid-cols-2 gap-4 text-xs">
          <Metric label="Version" value={engine.version} mono />
          <Metric label="Recursion limit" value={String(engine.recursion_limit)} />
          <Metric label="DB" value={engine.db_path} mono />
          <Metric label="Checkpoint DB" value={engine.checkpoint_db_path} mono />
        </CardContent>
      </Card>
    </section>
  );
}
