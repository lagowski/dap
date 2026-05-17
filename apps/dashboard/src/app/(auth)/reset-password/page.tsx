"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useResetPassword } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";

const schema = z
  .object({
    password: z.string().min(8, "Password must be at least 8 characters"),
    confirm: z.string(),
  })
  .refine((data) => data.password === data.confirm, {
    path: ["confirm"],
    message: "Passwords don't match",
  });

type ResetFormValues = z.infer<typeof schema>;

function ResetPasswordPageInner() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const reset = useResetPassword();
  const form = useForm<ResetFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { password: "", confirm: "" },
  });

  async function onSubmit(values: ResetFormValues) {
    await reset.mutateAsync({ token, password: values.password });
  }

  // Missing-token guard. ``/reset-password`` without a ``?token=`` is
  // unreachable from the email link — surface a friendly message
  // instead of letting the user type a password into a form that
  // can't succeed.
  if (!token) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Reset link missing</CardTitle>
          <CardDescription>
            This page requires a reset token. Open the link from your reset
            email, or request a new one.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild className="w-full">
            <Link href="/forgot-password">Request a new link</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  // Success — the cookie isn't set (reset doesn't auto-login by
  // design), so we send the user to /login to sign in with the new
  // credentials. Audit trail is cleaner: explicit login on the new
  // password.
  if (reset.isSuccess) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Password updated</CardTitle>
          <CardDescription>
            Your new password is active. Sign in to continue.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild className="w-full">
            <Link href="/login">Go to sign in</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose a new password</CardTitle>
        <CardDescription>
          Set a password you don&apos;t use anywhere else.
        </CardDescription>
      </CardHeader>
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="password">New password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              autoFocus
              disabled={reset.isPending}
              suppressHydrationWarning
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
              disabled={reset.isPending}
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
          {reset.isError && (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {formatApiError(reset.error)}
            </p>
          )}
        </CardContent>
        <CardFooter>
          <Button type="submit" className="w-full" disabled={reset.isPending}>
            {reset.isPending ? "Updating…" : "Update password"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordPageInner />
    </Suspense>
  );
}
