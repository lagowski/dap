import { Sidebar } from "@/components/sidebar";
import { ActiveProjectProvider } from "@/lib/active-project";

/**
 * Application chrome shared by every "logged-in" page (runs, agents,
 * pipelines, projects, settings). Lives in a route group so the auth
 * pages under ``(auth)`` can render without the sidebar — same
 * underlying ``app/layout.tsx`` provides the HTML + React Query
 * provider in both cases.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <ActiveProjectProvider>
      <div className="flex h-full">
        <Sidebar />
        <main className="flex-1 overflow-auto bg-muted/30">{children}</main>
      </div>
    </ActiveProjectProvider>
  );
}
