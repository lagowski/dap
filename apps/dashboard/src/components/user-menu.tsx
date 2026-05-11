"use client";

import { useRouter } from "next/navigation";
import { LogOut, ShieldCheck, UserCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useCurrentUser, useLogout } from "@/hooks/api";
import { cn } from "@/lib/utils";

/**
 * Footer chrome for the dashboard sidebar — shows the signed-in
 * user's email + a logout button.
 *
 * Three render states:
 *
 * - **Loading** (initial mount, before ``/api/auth/me`` returns) →
 *   a muted skeleton. Avoids flashing "Sign in" between an
 *   authenticated visit and the hydration that proves it.
 * - **Anonymous** (``useCurrentUser`` returned ``null``) → a
 *   "Sign in" CTA. Only reachable in dev: the middleware should
 *   have already redirected to ``/login`` before this layout
 *   renders. The CTA is the safety net for that contract.
 * - **Signed in** → email + admin badge (when ``is_superuser``) +
 *   logout button.
 *
 * Lives in the sidebar (#300, sub-B4) rather than the main content
 * area so it follows the user across every protected page without
 * each route having to render it.
 */
export function UserMenu() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const logout = useLogout();

  async function onLogout() {
    await logout.mutateAsync();
    // ``replace`` so the back button skips the (now-stale)
    // signed-in pages — they'd just 401-redirect anyway, but a
    // clean nav stack is friendlier.
    router.replace("/login");
  }

  if (currentUser.isLoading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading user"
        className="border-t p-3 text-xs text-muted-foreground"
      >
        <div className="h-3 w-24 animate-pulse rounded bg-muted" />
      </div>
    );
  }

  const user = currentUser.data;
  if (!user) {
    // The middleware should always send anonymous visitors to /login
    // before they reach a page that mounts this component. If we end
    // up here in production it's a bug — surface a manual sign-in
    // link instead of crashing.
    return (
      <div className="border-t p-3 text-sm">
        <Button
          variant="outline"
          className="w-full"
          onClick={() => router.replace("/login")}
        >
          Sign in
        </Button>
      </div>
    );
  }

  return (
    <div className="border-t p-3 space-y-2 text-sm">
      <div className="flex items-center gap-2 min-w-0">
        <UserCircle className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="truncate font-medium" title={user.email}>
          {user.email}
        </span>
        {user.is_superuser && (
          <span
            title="Administrator"
            className={cn(
              "ml-auto inline-flex shrink-0 items-center gap-1",
              "rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold",
              "text-amber-900",
            )}
          >
            <ShieldCheck className="h-3 w-3" aria-hidden />
            admin
          </span>
        )}
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={onLogout}
        disabled={logout.isPending}
        className="w-full justify-start gap-2 text-muted-foreground hover:text-foreground"
      >
        <LogOut className="h-4 w-4" aria-hidden />
        {logout.isPending ? "Signing out…" : "Sign out"}
      </Button>
    </div>
  );
}
