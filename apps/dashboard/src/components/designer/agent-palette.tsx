"use client";

import { Plus } from "lucide-react";
import { useAgentsList } from "@/hooks/api";
import { Badge } from "@/components/ui/badge";

interface AgentPaletteProps {
  onAddNode: (agentId: string, agentName: string) => void;
}

export function AgentPalette({ onAddNode }: AgentPaletteProps) {
  const { data, isPending, isError } = useAgentsList();

  return (
    <aside className="w-64 shrink-0 border-r bg-background overflow-y-auto">
      <div className="p-3 border-b">
        <h3 className="text-xs font-semibold uppercase text-muted-foreground">
          Agents
        </h3>
        <p className="text-xs text-muted-foreground mt-1">
          Click to add as node
        </p>
      </div>

      <div className="p-2 space-y-1">
        {isPending && (
          <p className="text-xs text-muted-foreground p-2">Loading…</p>
        )}
        {isError && (
          <p className="text-xs text-destructive p-2">Failed to load agents</p>
        )}
        {data && data.items.length === 0 && (
          <p className="text-xs text-muted-foreground p-2">
            No agents yet. Create one first.
          </p>
        )}
        {data?.items.map((agent) => (
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
      </div>
    </aside>
  );
}
