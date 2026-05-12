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
import { useCurrentUser, useLogin } from "@/hooks/api";
import { formatApiError } from "@/lib/api/client";

const schema = z.object({
  email: z.string().email("Enter a valid email"),
  password: z.string().min(1, "Password is required"),
});

type LoginFormValues = z.infer<typeof schema>;

/**
 * Where to send the user after a successful login. ``?next=`` is set
 * by the middleware redirect when an unauthenticated visit lands on
 * a protected route. We restrict the target to same-origin paths
 * (must start with ``/``, must not start with ``//`` — that would be
 * a protocol-relative URL pointing at another host) so a crafted
 * link can't bounce the user to a phishing page after login.
 */
function sanitiseNext(raw: string | null): string {
  if (!raw) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

function LoginPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = sanitiseNext(searchParams.get("next"));
  const oauthError = searchParams.get("oauth_error");

  const currentUser = useCurrentUser();
  const login = useLogin();

  // Already-authenticated visitors get bounced straight to ``next`` —
  // common when ``/login`` is reached via a stale bookmark or the
  // browser back button after a successful sign-in.
  useEffect(() => {
    if (currentUser.data) {
      router.replace(next);
    }
  }, [currentUser.data, next, router]);

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: LoginFormValues) {
    await login.mutateAsync(values);
    // ``router.replace`` rather than ``push`` so the back button
    // doesn't drop the user back on the login form.
    router.replace(next);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
        <CardDescription>Welcome back to DAP.</CardDescription>
      </CardHeader>
      {oauthError && (
        <div className="px-6 pb-4">
          <p
            role="alert"
            className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {oauthError}
          </p>
        </div>
      )}
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
              disabled={login.isPending}
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
              autoComplete="current-password"
              disabled={login.isPending}
              {...form.register("password")}
              aria-invalid={form.formState.errors.password ? true : undefined}
            />
            {form.formState.errors.password && (
              <p className="text-sm text-destructive">
                {form.formState.errors.password.message}
              </p>
            )}
          </div>
          {login.isError && (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {formatApiError(login.error)}
            </p>
          )}
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          <Button type="submit" className="w-full" disabled={login.isPending}>
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
          <div className="flex w-full justify-between text-sm text-muted-foreground">
            <Link href="/forgot-password" className="hover:underline">
              Forgot password?
            </Link>
            <Link href="/signup" className="hover:underline">
              Create account
            </Link>
          </div>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function LoginPage() {
  // ``useSearchParams`` requires a Suspense boundary on initial render
  // when the page is statically generated.
  return (
    <Suspense fallback={null}>
      <LoginPageInner />
    </Suspense>
  );
}
