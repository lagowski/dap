"use client";

import { AlertTriangle, CheckCircle2, Loader2, XCircle } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAdminSettings } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import type { AdminInstanceSettings } from "@/lib/api/types";

/**
 * Visual indicator for a presence flag. Green check when set, muted
 * X otherwise — matches the convention the runtime/provider settings
 * page uses, so admins don't have to re-learn the iconography.
 */
function StatusBadge({ ok, okLabel, missingLabel }: {
  ok: boolean;
  okLabel: string;
  missingLabel: string;
}) {
  if (ok) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-700">
        <CheckCircle2 className="h-3 w-3" aria-hidden />
        {okLabel}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <XCircle className="h-3 w-3" aria-hidden />
      {missingLabel}
    </span>
  );
}

function Row({ label, value, hint }: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="grid grid-cols-[180px_1fr] items-start gap-3 py-2 border-b last:border-b-0">
      <div className="text-xs uppercase tracking-wider text-muted-foreground pt-0.5">
        {label}
      </div>
      <div className="text-sm space-y-1">
        <div>{value}</div>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
    </div>
  );
}

function formatSeconds(seconds: number): string {
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds}s`;
}

export default function AdminSettingsPage() {
  const settings = useAdminSettings();

  if (settings.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading settings…
      </div>
    );
  }
  if (settings.isError || !settings.data) {
    return (
      <p className="p-6 text-sm text-destructive" role="alert">
        {formatApiError(settings.error)}
      </p>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold">Instance settings</h1>
        <p className="text-sm text-muted-foreground">
          Read-only snapshot of the engine&apos;s configuration. Changes are
          env-var driven and require an engine restart.
        </p>
      </div>

      <AuthCard data={settings.data} />
      <OAuthCard data={settings.data} />
      <CorsCard data={settings.data} />
      <StorageCard data={settings.data} />
    </div>
  );
}

function AuthCard({ data }: { data: AdminInstanceSettings }) {
  const { auth } = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Authentication</CardTitle>
        <CardDescription>
          JWT lifetime + the security-sensitive reset-token logging flag.
        </CardDescription>
      </CardHeader>
      <CardContent className="py-0">
        <Row
          label="JWT secret"
          value={
            <StatusBadge
              ok={auth.jwt_secret_configured}
              okLabel="configured"
              missingLabel="not configured"
            />
          }
          hint="Set via DAP_AUTH_JWT_SECRET. When unset the engine generates a per-process random — fine for dev, but multi-worker / multi-replica deployments must set this explicitly."
        />
        <Row
          label="Access TTL"
          value={
            <code className="text-xs">{formatSeconds(auth.access_ttl_seconds)}</code>
          }
          hint="DAP_AUTH_ACCESS_TTL_SECONDS. Bound on revocation latency — short enough that token theft has limited blast radius."
        />
        <Row
          label="Reset-token logging"
          value={
            auth.log_reset_tokens ? (
              <span className="inline-flex items-center gap-1 text-xs text-amber-700">
                <AlertTriangle className="h-3 w-3" aria-hidden />
                ON — dev only
              </span>
            ) : (
              <StatusBadge ok={true} okLabel="off (recommended)" missingLabel="" />
            )
          }
          hint="DAP_AUTH_LOG_RESET_TOKENS=1 makes the engine log raw reset tokens at WARNING. Useful for self-hosted dev without email; production logs would otherwise carry account-takeover material. Turn off before going live."
        />
      </CardContent>
    </Card>
  );
}

function OAuthCard({ data }: { data: AdminInstanceSettings }) {
  const { oauth } = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>OAuth providers</CardTitle>
        <CardDescription>
          Each provider activates only when both client_id and client_secret are
          set; partial config is treated as &quot;off&quot;.
        </CardDescription>
      </CardHeader>
      <CardContent className="py-0">
        <Row
          label="GitHub"
          value={
            <StatusBadge
              ok={oauth.github.configured}
              okLabel="configured"
              missingLabel="not configured"
            />
          }
          hint="DAP_OAUTH_GITHUB_CLIENT_ID + DAP_OAUTH_GITHUB_CLIENT_SECRET"
        />
        <Row
          label="Google"
          value={
            <StatusBadge
              ok={oauth.google.configured}
              okLabel="configured"
              missingLabel="not configured"
            />
          }
          hint="DAP_OAUTH_GOOGLE_CLIENT_ID + DAP_OAUTH_GOOGLE_CLIENT_SECRET"
        />
        <Row
          label="Redirect URL"
          value={
            oauth.redirect_url ? (
              <code className="text-xs break-all">{oauth.redirect_url}</code>
            ) : (
              <StatusBadge
                ok={false}
                okLabel=""
                missingLabel="JSON-callback fallback (no dashboard wired)"
              />
            )
          }
          hint="DAP_AUTH_OAUTH_REDIRECT_URL. Without this the OAuth callback returns the JWT as JSON — the dashboard can't intercept it."
        />
      </CardContent>
    </Card>
  );
}

function CorsCard({ data }: { data: AdminInstanceSettings }) {
  const { origins, using_default } = data.cors;
  return (
    <Card>
      <CardHeader>
        <CardTitle>CORS</CardTitle>
        <CardDescription>
          Effective origin allow-list. When DAP_CORS_ORIGINS is unset the engine
          falls back to a local-dev allow-list (not a permissive policy).
        </CardDescription>
      </CardHeader>
      <CardContent className="py-0">
        <Row
          label="Origins"
          value={
            <div className="space-y-2">
              {using_default && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-700">
                  <AlertTriangle className="h-3 w-3" aria-hidden />
                  using built-in dev defaults &mdash; set DAP_CORS_ORIGINS for prod
                </span>
              )}
              {origins.length === 0 ? (
                <span className="inline-flex items-center gap-1 text-xs text-destructive">
                  <AlertTriangle className="h-3 w-3" aria-hidden />
                  empty allow-list &mdash; all cross-origin requests will be rejected
                </span>
              ) : (
                <ul className="text-xs space-y-0.5">
                  {origins.map((origin) => (
                    <li key={origin}>
                      <code className="break-all">{origin}</code>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          }
        />
      </CardContent>
    </Card>
  );
}

function StorageCard({ data }: { data: AdminInstanceSettings }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Storage</CardTitle>
        <CardDescription>
          Where the engine persists agents, pipelines, runs, and auth state.
        </CardDescription>
      </CardHeader>
      <CardContent className="py-0">
        <Row
          label="Backend"
          value={<code className="text-xs">{data.storage.backend}</code>}
        />
        <Row
          label="Location"
          value={<code className="text-xs break-all">{data.storage.location}</code>}
          hint={
            data.storage.backend === "postgresql"
              ? "Database password is redacted server-side; the engine never returns it."
              : undefined
          }
        />
      </CardContent>
    </Card>
  );
}
