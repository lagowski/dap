"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  FolderKanban,
  GitBranch,
  Settings,
  ShieldCheck,
  User,
  Users,
  Workflow,
} from "lucide-react";
import { ProjectPicker } from "@/components/project-picker";
import { UserMenu } from "@/components/user-menu";
import { useCurrentUser } from "@/hooks/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/runs", label: "Runs", icon: Activity },
  { href: "/pipelines", label: "Pipelines", icon: GitBranch },
  { href: "/agents", label: "Agents", icon: Users },
  { href: "/account", label: "Account", icon: User },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

// Admin-only nav items. Rendered as a separate section beneath the
// regular ones, but only when ``currentUser.is_superuser`` is true.
// The links themselves are also server-side gated — a non-admin who
// types ``/admin`` into the address bar hits the layout's redirect
// even if the sidebar would have hidden the entry point (#301, C1).
const ADMIN_NAV = [{ href: "/admin", label: "Admin", icon: ShieldCheck }] as const;

export function Sidebar() {
  const pathname = usePathname();
  const currentUser = useCurrentUser();
  const isAdmin = currentUser.data?.is_superuser === true;
  return (
    <aside className="w-56 shrink-0 border-r bg-background flex flex-col">
      <div className="p-4 border-b flex items-center gap-2">
        <Workflow className="h-5 w-5" aria-hidden />
        <span className="font-semibold">DAP</span>
      </div>
      <ProjectPicker />
      <nav className="flex-1 p-2 space-y-1">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}
        {isAdmin && (
          <div className="pt-3 mt-3 border-t space-y-1">
            <p className="px-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Admin
            </p>
            {ADMIN_NAV.map(({ href, label, icon: Icon }) => {
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    active
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </Link>
              );
            })}
          </div>
        )}
      </nav>
      <UserMenu />
      <div className="p-3 border-t text-xs text-muted-foreground">
        <span className="font-mono">F6 MVP</span>
      </div>
    </aside>
  );
}
