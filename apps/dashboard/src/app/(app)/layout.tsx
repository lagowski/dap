import { AssistantPanel } from "@/components/assistant/assistant-panel";
import { AssistantPrefillProvider } from "@/components/assistant/assistant-prefill";
import { ConfirmDestructiveProvider } from "@/components/confirm-destructive-dialog";
import { Sidebar } from "@/components/sidebar";
import { ToastProvider } from "@/components/ui/toast";
import { ActiveProjectProvider } from "@/lib/active-project";

/**
 * Application chrome shared by every "logged-in" page (runs, agents,
 * pipelines, projects, settings). Lives in a route group so the auth
 * pages under ``(auth)`` can render without the sidebar — same
 * underlying ``app/layout.tsx`` provides the HTML + React Query
 * provider in both cases.
 *
 * ``ConfirmDestructiveProvider`` is mounted here (not at the global
 * ``app/layout.tsx``) so the imperative ``useConfirmDestructive``
 * hook is only reachable from authenticated pages — destructive
 * actions live exclusively in the ``(app)`` tree, and scoping the
 * provider keeps the dialog state out of ``(auth)`` which has no
 * destructive surface to confirm.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <ActiveProjectProvider>
      <ConfirmDestructiveProvider>
        <ToastProvider>
          <AssistantPrefillProvider>
            <div className="flex h-full">
              <Sidebar />
              <main className="flex-1 overflow-auto bg-muted/30">{children}</main>
              <AssistantPanel />
            </div>
          </AssistantPrefillProvider>
        </ToastProvider>
      </ConfirmDestructiveProvider>
    </ActiveProjectProvider>
  );
}
