"use client";

import { useState } from "react";
import {
  CheckCircle2,
  Loader2,
  ShieldCheck,
  ShieldOff,
  Trash2,
  UserPlus,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  useAdminUsers,
  useCurrentUser,
  useDeleteAdminUser,
  useUpdateAdminUser,
} from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";
import type { AdminUser } from "@/lib/api/types";
import { useConfirmDestructive } from "@/components/confirm-destructive-dialog";
import { cn } from "@/lib/utils";

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  // ``new Date(<garbage>)`` returns an ``Invalid Date`` object instead of
  // throwing, and ``toLocaleString`` then yields the literal string
  // "Invalid Date" — surface the raw value as a fallback so the admin
  // can at least see what came over the wire.
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export default function AdminUsersPage() {
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const users = useAdminUsers({ includeDeleted });
  const currentUser = useCurrentUser();
  const updateUser = useUpdateAdminUser();
  const deleteUser = useDeleteAdminUser();
  const confirmDestructive = useConfirmDestructive();

  // Track which user a mutation is currently in flight for so we can
  // disable that row's actions individually rather than the whole
  // table. ``React Query``'s ``mutation.variables`` exposes what
  // was passed in — we read the id from there.
  const pendingUpdateId =
    updateUser.isPending && updateUser.variables ? updateUser.variables.id : null;
  const pendingDeleteId =
    deleteUser.isPending && typeof deleteUser.variables === "string"
      ? deleteUser.variables
      : null;

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Users</h1>
          <p className="text-sm text-muted-foreground">
            Toggle admin role, suspend or revive accounts, soft-delete inactive
            members.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={includeDeleted}
            onChange={(e) => setIncludeDeleted(e.target.checked)}
            className="h-4 w-4"
          />
          Show soft-deleted
        </label>
      </div>

      {(updateUser.isError || deleteUser.isError) && (
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {formatApiError(updateUser.error ?? deleteUser.error)}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UserPlus className="h-5 w-5 text-muted-foreground" aria-hidden />
            {users.data
              ? `${users.data.total} ${users.data.total === 1 ? "user" : "users"}`
              : "Users"}
          </CardTitle>
          <CardDescription>
            Per-user actions persist immediately. Soft-deleted users keep their
            ownership history; pipelines and runs they triggered stay intact.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {users.isLoading && (
            <div className="flex items-center gap-2 px-6 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Loading users…
            </div>
          )}
          {users.isError && (
            <p className="px-6 py-8 text-sm text-destructive">
              {formatApiError(users.error)}
            </p>
          )}
          {users.data && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
                    <th className="px-4 py-2 font-medium">Email</th>
                    <th className="px-4 py-2 font-medium">Role</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                    <th className="px-4 py-2 font-medium">Joined</th>
                    <th className="px-4 py-2 font-medium">Last login</th>
                    <th className="px-4 py-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.data.items.map((user) => (
                    <UserRow
                      key={user.id}
                      user={user}
                      isSelf={user.id === currentUser.data?.id}
                      isUpdating={pendingUpdateId === user.id}
                      isDeleting={pendingDeleteId === user.id}
                      onToggleRole={() =>
                        updateUser.mutate({
                          id: user.id,
                          payload: { is_superuser: !user.is_superuser },
                        })
                      }
                      onToggleActive={() =>
                        updateUser.mutate({
                          id: user.id,
                          payload: { is_active: !user.is_active },
                        })
                      }
                      onDelete={async () => {
                        const ok = await confirmDestructive({
                          title: "Soft-delete user",
                          description: `Soft-delete ${user.email}? They will lose access immediately; ownership history is preserved.`,
                          confirmLabel: "Soft-delete",
                        });
                        if (ok) {
                          deleteUser.mutate(user.id);
                        }
                      }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

interface UserRowProps {
  user: AdminUser;
  isSelf: boolean;
  isUpdating: boolean;
  isDeleting: boolean;
  onToggleRole: () => void;
  onToggleActive: () => void;
  onDelete: () => void;
}

function UserRow({
  user,
  isSelf,
  isUpdating,
  isDeleting,
  onToggleRole,
  onToggleActive,
  onDelete,
}: UserRowProps) {
  const isSoftDeleted = user.deleted_at !== null;
  const busy = isUpdating || isDeleting;

  return (
    <tr
      className={cn(
        "border-b last:border-b-0 hover:bg-accent/30",
        isSoftDeleted && "opacity-60",
      )}
    >
      <td className="px-4 py-3 align-middle">
        <span className="font-medium">{user.email}</span>
        {isSelf && (
          <span className="ml-2 text-xs text-muted-foreground">(you)</span>
        )}
      </td>
      <td className="px-4 py-3 align-middle">
        {user.is_superuser ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900">
            <ShieldCheck className="h-3 w-3" aria-hidden />
            admin
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">member</span>
        )}
      </td>
      <td className="px-4 py-3 align-middle">
        {isSoftDeleted ? (
          <span className="inline-flex items-center gap-1 text-xs text-destructive">
            <Trash2 className="h-3 w-3" aria-hidden />
            deleted
          </span>
        ) : user.is_active ? (
          <span className="inline-flex items-center gap-1 text-xs text-emerald-700">
            <CheckCircle2 className="h-3 w-3" aria-hidden />
            active
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <XCircle className="h-3 w-3" aria-hidden />
            suspended
          </span>
        )}
      </td>
      <td className="px-4 py-3 align-middle text-xs text-muted-foreground">
        {formatTimestamp(user.created_at)}
      </td>
      <td className="px-4 py-3 align-middle text-xs text-muted-foreground">
        {formatTimestamp(user.last_login_at)}
      </td>
      <td className="px-4 py-3 align-middle text-right space-x-1">
        {(() => {
          // Same string is used for ``title`` (sighted hover) and
          // ``aria-label`` (screen readers) — without an aria-label
          // the icon-only buttons are invisible to assistive tech.
          const roleLabel = isSelf
            ? "You can't change your own role here"
            : user.is_superuser
              ? `Demote ${user.email} to member`
              : `Promote ${user.email} to admin`;
          const activeLabel = isSelf
            ? "You can't suspend yourself"
            : user.is_active
              ? `Suspend ${user.email}`
              : `Reactivate ${user.email}`;
          const deleteLabel = isSelf
            ? "You can't soft-delete yourself"
            : `Soft-delete ${user.email}`;
          return (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={onToggleRole}
                disabled={busy || isSelf || isSoftDeleted}
                title={roleLabel}
                aria-label={roleLabel}
              >
                {user.is_superuser ? (
                  <ShieldOff className="h-4 w-4" aria-hidden />
                ) : (
                  <ShieldCheck className="h-4 w-4" aria-hidden />
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onToggleActive}
                disabled={busy || isSelf || isSoftDeleted}
                title={activeLabel}
                aria-label={activeLabel}
              >
                {user.is_active ? (
                  <XCircle className="h-4 w-4" aria-hidden />
                ) : (
                  <CheckCircle2 className="h-4 w-4" aria-hidden />
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onDelete}
                disabled={busy || isSelf || isSoftDeleted}
                title={deleteLabel}
                aria-label={deleteLabel}
                className="text-destructive hover:text-destructive"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </Button>
            </>
          );
        })()}
      </td>
    </tr>
  );
}
