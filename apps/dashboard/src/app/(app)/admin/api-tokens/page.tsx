"use client";

import { useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  KeyRound,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  useAdminApiTokens,
  useAdminRevokeApiToken,
  useCreateApiToken,
  useCurrentUser,
} from "@/hooks/api";
import { formatApiError, type ApiTokenCreateResult } from "@/lib/api/client";
import type { AdminApiToken } from "@/lib/api/types";
import { useConfirmDestructive } from "@/components/confirm-destructive-dialog";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

/**
 * Compute the token's lifecycle bucket for the badge cell.
 * Order matters: "revoked" wins over "expired" so a revoked-then-
 * expired token doesn't visually downgrade to a softer state.
 */
function tokenStatus(token: AdminApiToken): "revoked" | "expired" | "active" {
  if (token.revoked_at) return "revoked";
  if (token.expires_at && new Date(token.expires_at) < new Date()) {
    return "expired";
  }
  return "active";
}

/**
 * "New token" button + dialog. Mints a token via the JWT-authenticated
 * dashboard session and shows the raw ``dap_<...>`` value ONCE (the engine
 * only stores its hash). Closing or navigating away loses it for good.
 */
function CreateTokenDialog({ onCreated }: { onCreated?: () => void }) {
  const create = useCreateApiToken();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [expiresDays, setExpiresDays] = useState("");
  const [created, setCreated] = useState<ApiTokenCreateResult | null>(null);
  const [copied, setCopied] = useState(false);

  const reset = () => {
    setName("");
    setExpiresDays("");
    setCreated(null);
    setCopied(false);
    // Only clear mutation state if it actually ran — avoids needless churn
    // on a fresh open.
    if (create.isError || create.isSuccess) create.reset();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const raw = expiresDays.trim();
    const parsed = raw === "" ? null : Number(raw);
    // Treat empty / 0 / negative / non-finite as "never expires". The HTML
    // ``min`` only guards the stepper, not typed input, and 0 would mint a
    // token that expires immediately.
    const expires =
      parsed !== null && Number.isFinite(parsed) && parsed >= 1
        ? Math.floor(parsed)
        : null;
    try {
      const result = await create.mutateAsync({
        name: name.trim(),
        expires_in_days: expires,
      });
      setCreated(result);
      onCreated?.();
    } catch {
      // error surfaced via create.error below
    }
  };

  const handleCopy = async () => {
    if (!created) return;
    await navigator.clipboard.writeText(created.token);
    setCopied(true);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        // Reset on every toggle so reopening never shows a stale token,
        // half-filled form, or previous error.
        reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="h-4 w-4 mr-1" aria-hidden="true" />
          New token
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New API token</DialogTitle>
          <DialogDescription>
            For the DAP CLI / scripts. The raw value is shown once — copy it
            before closing.
          </DialogDescription>
        </DialogHeader>

        {created ? (
          <div className="space-y-3">
            <p className="text-sm">
              Token{" "}
              <span className="font-medium">{created.name}</span> created. Copy
              it now — it can&apos;t be shown again.
            </p>
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={created.token}
                className="font-mono text-xs"
                onFocus={(e) => e.currentTarget.select()}
                aria-label="New API token value"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleCopy}
              >
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Use it as <code>DAP_AUTH_TOKEN</code> (or <code>--token</code>) for
              the CLI.
            </p>
            <DialogFooter>
              <Button
                type="button"
                onClick={() => {
                  setOpen(false);
                  reset();
                }}
              >
                Done
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="new-token-name">Name</Label>
              <Input
                id="new-token-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. cortex-cli"
                maxLength={255}
                required
                autoFocus
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="new-token-expiry">
                Expires in (days) — optional
              </Label>
              <Input
                id="new-token-expiry"
                type="number"
                min={1}
                max={3650}
                value={expiresDays}
                onChange={(e) => setExpiresDays(e.target.value)}
                placeholder="never"
              />
            </div>
            {create.isError ? (
              <p className="text-sm text-destructive" role="alert">
                {formatApiError(create.error)}
              </p>
            ) : null}
            <DialogFooter>
              <DialogClose asChild>
                <Button type="button" variant="outline">
                  Cancel
                </Button>
              </DialogClose>
              <Button
                type="submit"
                disabled={create.isPending || name.trim() === ""}
              >
                {create.isPending ? "Creating…" : "Create token"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function AdminApiTokensPage() {
  const [includeRevoked, setIncludeRevoked] = useState(true);
  const [offset, setOffset] = useState(0);

  const currentUser = useCurrentUser();
  const tokens = useAdminApiTokens({
    includeRevoked,
    offset,
    limit: PAGE_SIZE,
  });
  const revoke = useAdminRevokeApiToken();
  const confirmDestructive = useConfirmDestructive();

  const pendingRevokeId =
    revoke.isPending && typeof revoke.variables === "string"
      ? revoke.variables
      : null;

  const total = tokens.data?.total ?? 0;
  const hasNextPage = offset + PAGE_SIZE < total;
  const hasPrevPage = offset > 0;

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">API tokens</h1>
          <p className="text-sm text-muted-foreground">
            Every CLI / script token across the instance. Revoke instantly if
            one leaks.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input
              type="checkbox"
              checked={includeRevoked}
              onChange={(e) => {
                setIncludeRevoked(e.target.checked);
                setOffset(0);
              }}
              className="h-4 w-4"
            />
            Show revoked
          </label>
          <CreateTokenDialog onCreated={() => setOffset(0)} />
        </div>
      </div>

      {revoke.isError && (
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {formatApiError(revoke.error)}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-muted-foreground" aria-hidden />
            {tokens.data ? (
              <>
                <span suppressHydrationWarning>{total.toLocaleString()}</span>{" "}
                {total === 1 ? "token" : "tokens"}
              </>
            ) : (
              "Tokens"
            )}
          </CardTitle>
          <CardDescription>
            Revoking a token sets ``revoked_at`` server-side — the row stays
            for audit; the secret stops authenticating immediately.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {tokens.isLoading && (
            <div className="flex items-center gap-2 px-6 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Loading tokens…
            </div>
          )}
          {tokens.isError && (
            <p className="px-6 py-8 text-sm text-destructive">
              {formatApiError(tokens.error)}
            </p>
          )}
          {tokens.data && tokens.data.items.length === 0 && (
            <p className="px-6 py-8 text-sm text-muted-foreground">
              No tokens to show.
            </p>
          )}
          {tokens.data && tokens.data.items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
                    <th className="px-4 py-2 font-medium">Owner</th>
                    <th className="px-4 py-2 font-medium">Name</th>
                    <th className="px-4 py-2 font-medium">Prefix</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                    <th className="px-4 py-2 font-medium">Created</th>
                    <th className="px-4 py-2 font-medium">Last used</th>
                    <th className="px-4 py-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tokens.data.items.map((token) => (
                    <TokenRow
                      key={token.id}
                      token={token}
                      isSelf={token.owner_id === currentUser.data?.id}
                      isRevoking={pendingRevokeId === token.id}
                      onRevoke={async () => {
                        const ok = await confirmDestructive({
                          title: "Revoke API token",
                          description: `Revoke "${token.name}" (owned by ${token.owner_email})? The token will stop authenticating immediately.`,
                          confirmLabel: "Revoke",
                        });
                        if (ok) {
                          revoke.mutate(token.id);
                        }
                      }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
        {tokens.data && total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t px-6 py-3 text-xs text-muted-foreground">
            <span>
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of{" "}
              <span suppressHydrationWarning>{total.toLocaleString()}</span>
            </span>
            <div className="space-x-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={!hasPrevPage}
                aria-label="Previous page"
              >
                <ChevronLeft className="h-3 w-3" aria-hidden />
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={!hasNextPage}
                aria-label="Next page"
              >
                <ChevronRight className="h-3 w-3" aria-hidden />
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

interface TokenRowProps {
  token: AdminApiToken;
  isSelf: boolean;
  isRevoking: boolean;
  onRevoke: () => void;
}

function TokenRow({ token, isSelf, isRevoking, onRevoke }: TokenRowProps) {
  const status = tokenStatus(token);
  const revokeLabel = `Revoke ${token.name} (${token.owner_email})`;

  return (
    <tr
      className={cn(
        "border-b last:border-b-0 hover:bg-accent/30",
        status !== "active" && "opacity-60",
      )}
    >
      <td className="px-4 py-3 align-middle">
        <span className="font-medium">{token.owner_email}</span>
        {isSelf && (
          <span className="ml-2 text-xs text-muted-foreground">(you)</span>
        )}
      </td>
      <td className="px-4 py-3 align-middle">{token.name}</td>
      <td className="px-4 py-3 align-middle font-mono text-xs text-muted-foreground">
        dap_{token.prefix}…
      </td>
      <td className="px-4 py-3 align-middle">
        {status === "revoked" && (
          <span className="text-xs font-medium text-destructive">revoked</span>
        )}
        {status === "expired" && (
          <span className="text-xs font-medium text-muted-foreground">
            expired
          </span>
        )}
        {status === "active" && (
          <span className="text-xs font-medium text-emerald-700">active</span>
        )}
      </td>
      <td className="px-4 py-3 align-middle text-xs text-muted-foreground">
        <span suppressHydrationWarning>{formatTimestamp(token.created_at)}</span>
      </td>
      <td className="px-4 py-3 align-middle text-xs text-muted-foreground">
        <span suppressHydrationWarning>{formatTimestamp(token.last_used_at)}</span>
      </td>
      <td className="px-4 py-3 align-middle text-right">
        {(() => {
          // Keep owner/token context in the label whether the button
          // is active or disabled — screen readers otherwise lose the
          // "what am I about to revoke?" anchor when status flips
          // (Copilot review on PR #330).
          const label =
            status === "revoked" ? `${revokeLabel} (already revoked)` : revokeLabel;
          return (
            <Button
              variant="ghost"
              size="sm"
              onClick={onRevoke}
              disabled={isRevoking || status === "revoked"}
              title={label}
              aria-label={label}
              className="text-destructive hover:text-destructive"
            >
              <Trash2 className="h-4 w-4" aria-hidden />
            </Button>
          );
        })()}
      </td>
    </tr>
  );
}
