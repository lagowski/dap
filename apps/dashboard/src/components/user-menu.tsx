"use client";

import { useRouter } from "next/navigation";
import { AlertCircle, LogOut, ShieldCheck, UserCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useCurrentUser, useLogout } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/**
 * Footer chrome for the dashboard sidebar — shows the signed-in
 * user's email + a logout button.
 *
 * Four render states:
 *
 * - **Loading** (initial mount, before ``/api/auth/me`` returns) →
 *   a muted skeleton. Avoids flashing "Sign in" between an
 *   authenticated visit and the hydration that proves it.
 * - **Error** (``useCurrentUser`` threw on something other than the
 *   401 path — network outage, server 500) → an inline error chip.
 *   Distinct from "Anonymous" so we don't lie to the user about
 *   being logged out when the truth is the engine is unreachable.
 * - **Anonymous** (``useCurrentUser`` returned ``null`` from the
 *   401 branch) → a "Sign in" CTA. Only reachable in dev: the
 *   middleware should have already redirected to ``/login`` before
 *   this layout renders. The CTA is the safety net for that
 *   contract — preserves the current path via ``?next=`` so the
 *   user comes back where they were.
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

  /**
   * Build the ``/login`` URL with the current page captured in
   * ``?next=`` so the user lands back here after authenticating.
   *
   * Reads ``window.location`` at click time rather than via Next's
   * ``usePathname`` / ``useSearchParams`` hooks. The hook-based
   * version would force every page under ``(app)/`` to bail out of
   * static rendering — since this component lives in the layout, the
   * deopt would ripple to *every* dashboard route. The click handler
   * only runs in the browser, so reading ``window`` directly is
   * safe and prerender-friendly.
   */
  function navigateToLogin() {
    const here = window.location.pathname + window.location.search;
    router.replace(`/login?next=${encodeURIComponent(here)}`);
  }

  function onLogout() {
    // ``mutate`` (not ``mutateAsync``) with onSuccess/onError so a
    // click handler can never throw an unhandled promise. On
    // success: navigate to /login. On error: stay put — the mutation
    // sets ``logout.error`` and the inline banner below surfaces it.
    logout.mutate(undefined, {
      onSuccess: () => {
        // ``replace`` so the back button skips the (now-stale)
        // signed-in pages — they'd just 401-redirect anyway, but
        // a clean nav stack is friendlier.
        router.replace("/login");
      },
    });
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

  // ``getCurrentUser`` swallows 401 → ``null``, so any error here is
  // a non-auth failure (network, 5xx, malformed JSON). Distinguishing
  // it from the anonymous state keeps the UI honest.
  if (currentUser.isError) {
    return (
      <div
        role="alert"
        className="border-t p-3 text-xs text-muted-foreground"
      >
        <div className="flex items-start gap-2">
          <AlertCircle
            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive"
            aria-hidden
          />
          <div className="space-y-1">
            <p className="font-medium text-foreground">Couldn&apos;t reach engine</p>
            <p className="text-[11px]">{formatApiError(currentUser.error)}</p>
          </div>
        </div>
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
        <Button variant="outline" className="w-full" onClick={navigateToLogin}>
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
      {logout.isError && (
        <p
          role="alert"
          className="text-[11px] text-destructive"
        >
          Sign-out failed: {formatApiError(logout.error)}
        </p>
      )}
    </div>
  );
}
