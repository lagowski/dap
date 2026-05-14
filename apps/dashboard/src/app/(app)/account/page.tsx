"use client";

import { useState } from "react";
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
import { formatApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

// 8 minimum mirrors the engine's UserManager.validate_password policy —
// letting a shorter value through would just be rejected server-side
// with a less-friendly error.
const schema = z
  .object({
    password: z.string().min(8, "Password must be at least 8 characters"),
    confirm: z.string(),
  })
  .refine((data) => data.password === data.confirm, {
    path: ["confirm"],
    message: "Passwords don't match",
  });

type FormValues = z.infer<typeof schema>;

export default function AccountPage() {
  const currentUser = useCurrentUser();
  const updatePassword = useUpdateMyPassword();
  const [success, setSuccess] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { password: "", confirm: "" },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    setSuccess(false);
    try {
      await updatePassword.mutateAsync(values.password);
      setSuccess(true);
      form.reset();
    } catch {
      // formatApiError on the mutation error surfaces the engine's
      // detail message below; nothing else to do here.
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
            <p className="text-muted-foreground">Loading…</p>
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
            Minimum 8 characters. The engine validates server-side.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="password">New password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="new-password"
                disabled={updatePassword.isPending}
                {...form.register("password")}
                aria-invalid={form.formState.errors.password ? true : undefined}
              />
              {form.formState.errors.password && (
                <p className="text-sm text-destructive">
                  {form.formState.errors.password.message}
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
                {...form.register("confirm")}
                aria-invalid={form.formState.errors.confirm ? true : undefined}
              />
              {form.formState.errors.confirm && (
                <p className="text-sm text-destructive">
                  {form.formState.errors.confirm.message}
                </p>
              )}
            </div>
            {updatePassword.isError && (
              <p
                role="alert"
                className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {formatApiError(updatePassword.error)}
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
