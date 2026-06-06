"use client";

import { useMemo, useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import { ChevronDown, ChevronRight, Plus, Search } from "lucide-react";
import { useAgentsList } from "@/hooks/api";
import { Badge } from "@/components/ui/badge";

interface AgentPaletteProps {
  onAddNode: (agentId: string, agentName: string) => void;
}

// Derive a coarse category from the agent name so the otherwise-flat
// palette collapses into scannable sections. "Cortex Phase N — …" agents
// group by phase; everything else falls into "Misc".
function agentCategory(name: string): string {
  const m = name.match(/^\s*Cortex\s+Phase\s+(\d+)/i);
  return m ? `Cortex Phase ${m[1]}` : "Misc";
}

// Cortex phases ascending, Misc (and anything unrecognised) last.
function categoryRank(cat: string): number {
  const m = cat.match(/^Cortex Phase (\d+)$/);
  return m ? Number(m[1]) : 999;
}

export function AgentPalette({ onAddNode }: AgentPaletteProps) {
  const { data, isPending, isError } = useAgentsList();
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());

  const groups = useMemo(() => {
    const items = data?.items ?? [];
    const q = query.trim().toLowerCase();
    const filtered = q
      ? items.filter(
          (a) =>
            a.name.toLowerCase().includes(q) ||
            a.role.toLowerCase().includes(q) ||
            a.runtime_id.toLowerCase().includes(q),
        )
      : items;

    const byCat = new Map<string, typeof filtered>();
    for (const a of filtered) {
      const cat = agentCategory(a.name);
      const arr = byCat.get(cat) ?? [];
      arr.push(a);
      byCat.set(cat, arr);
    }
    return [...byCat.entries()]
      .map(([cat, agents]) => ({ cat, agents }))
      .sort(
        (a, b) =>
          categoryRank(a.cat) - categoryRank(b.cat) ||
          a.cat.localeCompare(b.cat),
      );
  }, [data, query]);

  const toggle = (cat: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) {
        next.delete(cat);
      } else {
        next.add(cat);
      }
      return next;
    });

  // While filtering, force every matching group open so results aren't
  // hidden inside a section the user previously collapsed.
  const filtering = query.trim().length > 0;

  return (
    <aside className="w-64 shrink-0 border-r bg-background overflow-y-auto flex flex-col">
      <div className="p-3 border-b">
        <h3 className="text-xs font-semibold uppercase text-muted-foreground">
          Agents
        </h3>
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

      <div className="p-2 space-y-2">
        {isPending && (
          <LoadingState className="text-xs p-2" />
        )}
        {isError && (
          <p className="text-xs text-destructive p-2">Failed to load agents</p>
        )}
        {data && data.items.length === 0 && (
          <p className="text-xs text-muted-foreground p-2">
            No agents yet. Create one first.
          </p>
        )}
        {data && data.items.length > 0 && groups.length === 0 && (
          <p className="text-xs text-muted-foreground p-2">
            No agents match “{query}”.
          </p>
        )}

        {groups.map(({ cat, agents }) => {
          const isCollapsed = !filtering && collapsed.has(cat);
          return (
            <div key={cat} className="space-y-1">
              <button
                type="button"
                onClick={() => toggle(cat)}
                aria-expanded={!isCollapsed}
                className="w-full flex items-center gap-1 px-1 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
              >
                {isCollapsed ? (
                  <ChevronRight className="h-3 w-3 shrink-0" aria-hidden />
                ) : (
                  <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
                )}
                <span className="truncate">{cat}</span>
                <span className="ml-auto tabular-nums text-muted-foreground/70">
                  {agents.length}
                </span>
              </button>

              {!isCollapsed &&
                agents.map((agent) => (
                  <button
                    type="button"
                    key={agent.id}
                    onClick={() => onAddNode(agent.id, agent.name)}
                    className="w-full text-left rounded-md border bg-card p-2 hover:bg-accent transition-colors group"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium truncate">
                          {agent.name}
                        </div>
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
            </div>
          );
        })}
      </div>
    </aside>
  );
}
