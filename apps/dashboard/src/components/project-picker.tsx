"use client";

import Link from "next/link";
import { FolderKanban } from "lucide-react";
import { useActiveProject } from "@/lib/active-project";
import { useProjectsList } from "@/hooks/api";
import { cn } from "@/lib/utils";

const ALL_PROJECTS_VALUE = "__all__";

/**
 * Sidebar project picker (#68). Native ``<select>`` keeps the
 * dependency surface minimal — no popover/menu primitives needed for
 * a single dropdown that flips a global filter.
 */
export function ProjectPicker() {
  const { activeProjectId, setActiveProjectId, isHydrated } = useActiveProject();
  const { data, isPending } = useProjectsList();
  const projects = data?.items ?? [];

  // Pre-hydration we render "All projects" placeholder to keep the
  // server-rendered HTML stable; the effect in the provider will
  // restore the persisted value on mount.
  const value =
    isHydrated && activeProjectId !== null ? activeProjectId : ALL_PROJECTS_VALUE;

  const handleChange = (next: string) => {
    setActiveProjectId(next === ALL_PROJECTS_VALUE ? null : next);
  };

  // If the user archived (or deleted from another tab) the active
  // project, the dropdown would render a stale id. Show a synthetic
  // option so the label doesn't disappear; selecting any other entry
  // clears it.
  const activeKnown =
    activeProjectId === null ||
    projects.some((p) => p.id === activeProjectId);

  return (
    <div className="px-3 py-2 border-b">
      <label
        className="flex items-center gap-1.5 mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
        htmlFor="project-picker"
      >
        <FolderKanban className="h-3 w-3" aria-hidden="true" />
        Project scope
      </label>
      <select
        id="project-picker"
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        // Disable until hydration completes — otherwise a user click
        // during the brief pre-hydration window gets clobbered by the
        // hydration effect's setState. Also disabled during the
        // initial projects fetch so the dropdown isn't empty.
        disabled={!isHydrated || isPending}
        className={cn(
          "w-full h-8 rounded-md border border-input bg-background px-2 text-sm",
          "disabled:opacity-50 disabled:cursor-not-allowed",
        )}
      >
        <option value={ALL_PROJECTS_VALUE}>All projects</option>
        {!activeKnown && activeProjectId !== null ? (
          // Disabled so the user can't actively re-select the stale
          // id; switching to a valid entry clears it on next change.
          <option value={activeProjectId} disabled>
            {activeProjectId.slice(0, 8)}… (unavailable)
          </option>
        ) : null}
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <Link
        href="/projects"
        className="mt-1 block text-[11px] text-muted-foreground hover:text-foreground"
      >
        Manage projects →
      </Link>
    </div>
  );
}
