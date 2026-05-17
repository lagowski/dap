"use client";

import Link from "next/link";
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
import { useForgotPassword } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";

const schema = z.object({
  email: z.string().email("Enter a valid email"),
});

type ForgotFormValues = z.infer<typeof schema>;

export default function ForgotPasswordPage() {
  const forgot = useForgotPassword();
  const form = useForm<ForgotFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "" },
  });

  async function onSubmit(values: ForgotFormValues) {
    await forgot.mutateAsync(values.email);
  }

  // After submit, regardless of whether the email exists, show the
  // same confirmation panel — same anti-enumeration response the
  // engine sends (always 202). A caller cannot use this form to
  // probe which emails are registered.
  if (forgot.isSuccess) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Check your email</CardTitle>
          <CardDescription>
            If an account exists for that address, we&apos;ve sent a reset link.
            Open it to choose a new password.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild variant="outline" className="w-full">
            <Link href="/login">Back to sign in</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Forgot your password?</CardTitle>
        <CardDescription>
          Enter the email address on your account and we&apos;ll send a reset link.
        </CardDescription>
      </CardHeader>
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              autoFocus
              disabled={forgot.isPending}
              suppressHydrationWarning
              {...form.register("email")}
              aria-invalid={form.formState.errors.email ? true : undefined}
            />
            {form.formState.errors.email && (
              <p className="text-sm text-destructive">
                {form.formState.errors.email.message}
              </p>
            )}
          </div>
          {forgot.isError && (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {formatApiError(forgot.error)}
            </p>
          )}
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          <Button type="submit" className="w-full" disabled={forgot.isPending}>
            {forgot.isPending ? "Sending…" : "Send reset link"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Remembered it?{" "}
            <Link href="/login" className="hover:underline">
              Back to sign in
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}
