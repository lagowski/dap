"use client";

import { useState } from "react";
import { LoadingState } from "@/components/ui/spinner";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ShieldCheck, UserCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCurrentUser, useUpdateMyPassword } from "@/hooks/api";
import { formatApiError, login as loginApi } from "@/lib/api/client";
import { cn } from "@/lib/utils";

// 8 minimum mirrors the engine's UserManager.validate_password policy.
const schema = z
  .object({
    currentPassword: z.string().min(1, "Current password is required"),
    newPassword: z.string().min(8, "New password must be at least 8 characters"),
    confirm: z.string(),
  })
  .refine((data) => data.newPassword === data.confirm, {
    path: ["confirm"],
    message: "Passwords don't match",
  });

type FormValues = z.infer<typeof schema>;

export default function AccountPage() {
  const currentUser = useCurrentUser();
  const updatePassword = useUpdateMyPassword();
  const [success, setSuccess] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { currentPassword: "", newPassword: "", confirm: "" },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    setSuccess(false);
    setSubmitError(null);

    const email = currentUser.data?.email;
    if (!email) {
      setSubmitError("Couldn't read current session — try refreshing the page.");
      return;
    }

    // Verify the current password by re-logging in before calling the
    // generic PATCH /users/me. fastapi-users' default lets any active
    // session change the password without re-auth, so without this
    // step a stolen JWT would be enough to take over the account.
    // Login refreshes the cookie but doesn't invalidate caches (we
    // call the API helper directly, not the mutation hook).
    try {
      await loginApi({ email, password: values.currentPassword });
    } catch {
      form.setError("currentPassword", {
        message: "Current password is incorrect",
      });
      return;
    }

    try {
      await updatePassword.mutateAsync(values.newPassword);
      setSuccess(true);
      form.reset();
    } catch (err) {
      setSubmitError(formatApiError(err));
    }
  });

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Account</h1>
        <p className="text-sm text-muted-foreground">
          Manage your sign-in credentials.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Profile</CardTitle>
          <CardDescription>
            Email is set at signup and managed by the engine — change requires
            admin action.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {currentUser.isPending ? (
            <LoadingState />
          ) : currentUser.isError ? (
            <p className="text-destructive">
              Couldn&apos;t reach engine: {formatApiError(currentUser.error)}
            </p>
          ) : currentUser.data ? (
            <div className="flex items-center gap-2">
              <UserCircle className="h-4 w-4 text-muted-foreground" aria-hidden />
              <span className="font-medium">{currentUser.data.email}</span>
              {currentUser.data.is_superuser && (
                <span
                  className={cn(
                    "ml-2 inline-flex items-center gap-1 rounded-md bg-amber-100",
                    "px-1.5 py-0.5 text-[10px] font-semibold text-amber-900",
                  )}
                >
                  <ShieldCheck className="h-3 w-3" aria-hidden />
                  admin
                </span>
              )}
            </div>
          ) : (
            <p className="text-muted-foreground">Not signed in.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Change password</CardTitle>
          <CardDescription>
            Minimum 8 characters. The current password is required so a stolen
            session cookie alone can&apos;t change your credentials.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="currentPassword">Current password</Label>
              <Input
                id="currentPassword"
                type="password"
                autoComplete="current-password"
                disabled={updatePassword.isPending}
                suppressHydrationWarning
              {...form.register("currentPassword")}
                aria-invalid={
                  form.formState.errors.currentPassword ? true : undefined
                }
              />
              {form.formState.errors.currentPassword && (
                <p className="text-sm text-destructive">
                  {form.formState.errors.currentPassword.message}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="newPassword">New password</Label>
              <Input
                id="newPassword"
                type="password"
                autoComplete="new-password"
                disabled={updatePassword.isPending}
                suppressHydrationWarning
              {...form.register("newPassword")}
                aria-invalid={form.formState.errors.newPassword ? true : undefined}
              />
              {form.formState.errors.newPassword && (
                <p className="text-sm text-destructive">
                  {form.formState.errors.newPassword.message}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm">Confirm new password</Label>
              <Input
                id="confirm"
                type="password"
                autoComplete="new-password"
                disabled={updatePassword.isPending}
                suppressHydrationWarning
              {...form.register("confirm")}
                aria-invalid={form.formState.errors.confirm ? true : undefined}
              />
              {form.formState.errors.confirm && (
                <p className="text-sm text-destructive">
                  {form.formState.errors.confirm.message}
                </p>
              )}
            </div>
            {submitError && (
              <p
                role="alert"
                className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {submitError}
              </p>
            )}
            {success && (
              <p
                role="status"
                className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700"
              >
                Password updated.
              </p>
            )}
            <Button type="submit" disabled={updatePassword.isPending}>
              {updatePassword.isPending ? "Updating…" : "Update password"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
