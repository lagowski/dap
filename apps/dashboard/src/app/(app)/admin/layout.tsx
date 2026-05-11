"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck } from "lucide-react";

import { useCurrentUser } from "@/hooks/api";

/**
 * Admin section gate (#301, sub-C1).
 *
 * Renders only when the current user is a superuser. Non-admins are
 * redirected to the home page on mount — the actual backend enforcement
 * lives on every admin endpoint server-side (each repo function takes
 * ``is_admin`` and refuses cross-user / system-wide access otherwise),
 * so this gate is UX polish, not a security boundary. The bookmarked
 * "/admin/audit-log" URL of a recently-demoted user 404s on the API
 * regardless of what this layout decides.
 *
 * The plan called for a middleware-level gate; we deliberately do the
 * check here instead. Middleware would need to decode the JWT
 * (``jose`` dep, JWT secret access) and validating signatures
 * client-side would still leave the data on engine routes — so the
 * middleware would only be a UX guard, exactly what this layout is.
 * Keeping it in one place avoids two sources of truth.
 */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const currentUser = useCurrentUser();

  // Send non-admins home as soon as the auth probe resolves. The
  // effect fires twice on the loading→resolved transition; the
  // guard against ``isLoading`` keeps it idempotent.
  useEffect(() => {
    if (currentUser.isLoading) return;
    if (!currentUser.data?.is_superuser) {
      router.replace("/");
    }
  }, [currentUser.isLoading, currentUser.data, router]);

  if (currentUser.isLoading) {
    return (
      <div className="p-6 text-sm text-muted-foreground" aria-busy="true">
        Loading…
      </div>
    );
  }

  // Same condition as the redirect, but renders a clean blank state
  // while the router transition is in-flight. Without this the
  // non-admin would briefly see the admin chrome.
  if (!currentUser.data?.is_superuser) {
    return null;
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b bg-amber-50/60 px-6 py-3">
        <div className="flex items-center gap-2 text-sm text-amber-900">
          <ShieldCheck className="h-4 w-4" aria-hidden />
          <span className="font-medium">Admin</span>
          <span className="text-amber-800/70">
            — instance-wide controls, visible only to superusers
          </span>
        </div>
      </header>
      <div className="flex-1 overflow-auto">{children}</div>
    </div>
  );
}
