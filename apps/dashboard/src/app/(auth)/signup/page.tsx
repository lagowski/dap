"use client";

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import { OAuthButtons } from "@/components/oauth-buttons";
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
import { useCurrentUser, useRegister } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";

const schema = z
  .object({
    email: z.string().email("Enter a valid email"),
    // 8 minimum keeps the form aligned with the engine's
    // fastapi-users default (``min_length=8``) — letting a shorter
    // value through would only get rejected server-side.
    password: z
      .string()
      .min(8, "Password must be at least 8 characters"),
    confirm: z.string(),
  })
  .refine((data) => data.password === data.confirm, {
    path: ["confirm"],
    message: "Passwords don't match",
  });

type SignupFormValues = z.infer<typeof schema>;

function sanitiseNext(raw: string | null): string {
  if (!raw) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

function SignupPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = sanitiseNext(searchParams.get("next"));

  const currentUser = useCurrentUser();
  const register = useRegister();

  useEffect(() => {
    if (currentUser.data) {
      router.replace(next);
    }
  }, [currentUser.data, next, router]);

  const form = useForm<SignupFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "", confirm: "" },
  });

  async function onSubmit(values: SignupFormValues) {
    const result = await register.mutateAsync({
      email: values.email,
      password: values.password,
    });
    // When the engine accepted the registration but didn't auto-login
    // (e.g. email verification is required), keep the user here and
    // show the success card — redirecting would bounce them to
    // ``/login`` since the cookie isn't live. ``register.data`` drives
    // the success banner below.
    if (result.verified) {
      router.replace(next);
    }
  }

  // Pending-verification state — registration succeeded but the engine
  // didn't issue a session. We surface the email so the user knows
  // which inbox to check, and offer the sign-in entry point for after
  // they verify.
  if (register.data && !register.data.verified) {
    const email = form.getValues("email");
    return (
      <Card>
        <CardHeader>
          <CardTitle>Check your email</CardTitle>
          <CardDescription>
            We&apos;ve sent a verification link to <strong>{email}</strong>. Open it
            to activate your account, then sign in.
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
        <CardTitle>Create account</CardTitle>
        <CardDescription>
          Get started with DAP — your runs and pipelines stay private to you.
        </CardDescription>
      </CardHeader>
      <div className="px-6 pb-4">
        <OAuthButtons />
        <div className="relative my-4 text-center">
          <span className="bg-card px-2 text-xs uppercase tracking-wider text-muted-foreground">
            or
          </span>
          <div className="absolute inset-x-0 top-1/2 -z-10 h-px bg-border" />
        </div>
      </div>
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              autoFocus
              disabled={register.isPending}
              {...form.register("email")}
              aria-invalid={form.formState.errors.email ? true : undefined}
            />
            {form.formState.errors.email && (
              <p className="text-sm text-destructive">
                {form.formState.errors.email.message}
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              disabled={register.isPending}
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
            <Label htmlFor="confirm">Confirm password</Label>
            <Input
              id="confirm"
              type="password"
              autoComplete="new-password"
              disabled={register.isPending}
              {...form.register("confirm")}
              aria-invalid={form.formState.errors.confirm ? true : undefined}
            />
            {form.formState.errors.confirm && (
              <p className="text-sm text-destructive">
                {form.formState.errors.confirm.message}
              </p>
            )}
          </div>
          {register.isError && (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {formatApiError(register.error)}
            </p>
          )}
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          <Button type="submit" className="w-full" disabled={register.isPending}>
            {register.isPending ? "Creating account…" : "Create account"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="hover:underline">
              Sign in
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupPageInner />
    </Suspense>
  );
}
