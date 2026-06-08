"use client";

import { useMemo, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import { Plus, Search } from "lucide-react";
import { useAgentsList } from "@/hooks/api";
import { Badge } from "@/components/ui/badge";
import { isManagedAgent } from "@/lib/managed-agent";

interface AgentPaletteProps {
  onAddNode: (agentId: string, agentName: string) => void;
}

export function AgentPalette({ onAddNode }: AgentPaletteProps) {
  const { data, isPending, isError } = useAgentsList();
  const [query, setQuery] = useState("");

  // Managed (Cortex) agents are nodes of an imported bundle (#739) — you never
  // hand-place a single cortex node, the whole pipeline arrives via import. So
  // they're kept out of the palette entirely; only standalone agents are
  // placeable. A footnote notes how many are bundled away.
  const { visible, managedCount } = useMemo(() => {
    const items = data?.items ?? [];
    const managed = items.filter(isManagedAgent);
    const standalone = items.filter((a) => !isManagedAgent(a));
    const q = query.trim().toLowerCase();
    const matched = q
      ? standalone.filter(
          (a) =>
            a.name.toLowerCase().includes(q) ||
            a.role.toLowerCase().includes(q) ||
            a.runtime_id.toLowerCase().includes(q),
        )
      : standalone;
    return { visible: matched, managedCount: managed.length };
  }, [data, query]);

  const filtering = query.trim().length > 0;
  const hasAgents = (data?.items.length ?? 0) > 0;

  return (
    <aside className="w-64 shrink-0 border-r bg-background overflow-y-auto flex flex-col">
      <div className="p-3 border-b">
        <h3 className="text-xs font-semibold uppercase text-muted-foreground">Agents</h3>
        <p className="text-xs text-muted-foreground mt-1">Click to add as node</p>
        <div className="relative mt-2">
          <Search
            className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground"
            aria-hidden
          />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter agents…"
            aria-label="Filter agents"
            className="w-full rounded-md border bg-background pl-7 pr-2 py-1 text-xs outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      </div>

      <div className="p-2 space-y-1">
        {isPending && <LoadingState className="text-xs p-2" />}
        {isError && <p className="text-xs text-destructive p-2">Failed to load agents</p>}
        {data && !hasAgents && (
          <p className="text-xs text-muted-foreground p-2">No agents yet. Create one first.</p>
        )}
        {hasAgents && visible.length === 0 && (
          <p className="text-xs text-muted-foreground p-2">
            {filtering ? `No agents match “${query}”.` : "No standalone agents to place."}
          </p>
        )}

        {visible.map((agent) => (
          <button
            type="button"
            key={agent.id}
            onClick={() => onAddNode(agent.id, agent.name)}
            className="w-full text-left rounded-md border bg-card p-2 hover:bg-accent transition-colors group"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{agent.name}</div>
                <div className="flex items-center gap-1 mt-1 flex-wrap">
                  <Badge variant="secondary" className="text-[10px]">
                    {agent.role}
                  </Badge>
                  <span className="text-[10px] text-muted-foreground font-mono">
                    {agent.runtime_id}
                  </span>
                </div>
              </div>
              <Plus className="h-4 w-4 text-muted-foreground group-hover:text-foreground shrink-0 mt-0.5" />
            </div>
          </button>
        ))}

        {managedCount > 0 && (
          <p className="text-[10px] text-muted-foreground p-2 mt-1 border-t pt-2 leading-relaxed">
            {managedCount} Cortex agent{managedCount === 1 ? "" : "s"} are part of an imported
            bundle and aren’t placed individually.
          </p>
        )}
      </div>
    </aside>
  );
}
