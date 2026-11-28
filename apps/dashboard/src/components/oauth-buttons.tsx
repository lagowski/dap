"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";

/**
 * OAuth sign-in buttons for ``/login`` and ``/signup`` (#300, sub-B5).
 *
 * Each button is a plain link to the dashboard's
 * ``/api/auth/oauth/<provider>`` handler. The handler talks to the
 * engine, gets the provider's authorize URL, and bounces the
 * browser through the OAuth dance. Final callback lands at
 * ``/api/auth/oauth/callback`` which mints the cookie + sends the
 * user home — see those route handlers.
 *
 * Providers that aren't configured server-side (missing
 * ``DAP_OAUTH_<X>_CLIENT_ID`` / ``_SECRET``) produce a 503 from
 * the authorize handler; the button still renders but click results
 * in a friendly redirect to ``/login?oauth_error=...``. This keeps
 * the layout consistent across deployments without forcing the
 * client to know what's configured.
 */
export function OAuthButtons() {
  // ``prefetch={false}``: these endpoints issue server-side redirects
  // (and ultimately bounce the browser off-origin to the provider).
  // Prefetching them on hover would kick off OAuth flows the user
  // didn't actually click.
  return (
    <div className="grid gap-2">
      <Button asChild variant="outline" className="w-full gap-2">
        <Link href="/api/auth/oauth/github" prefetch={false}>
          <GitHubIcon className="h-4 w-4" aria-hidden />
          Continue with GitHub
        </Link>
      </Button>
      <Button asChild variant="outline" className="w-full gap-2">
        <Link href="/api/auth/oauth/google" prefetch={false}>
          <GoogleIcon className="h-4 w-4" aria-hidden />
          Continue with Google
        </Link>
      </Button>
    </div>
  );
}

/**
 * Inline Google "G" mark. lucide-react ships a generic ``Globe`` but
 * not a Google glyph — keep this tiny SVG inline so we don't pull
 * in a logos package for two icons.
 */
function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M12 .5C5.37.5 0 5.87 0 12.5c0 5.3 3.44 9.8 8.21 11.39.6.11.82-.26.82-.58v-2.03c-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.73.08-.73 1.21.09 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.12-3.17 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.65.25 2.87.12 3.17.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.62-5.49 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58A12.01 12.01 0 0 0 24 12.5C24 5.87 18.63.5 12 .5Z" />
    </svg>
  );
}

function GoogleIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.76h3.56c2.08-1.92 3.28-4.74 3.28-8.09Z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.56-2.76c-.98.66-2.24 1.06-3.72 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23Z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.11A6.6 6.6 0 0 1 5.5 12c0-.73.13-1.44.34-2.11V7.05H2.18A11 11 0 0 0 1 12c0 1.77.42 3.45 1.18 4.95l3.66-2.84Z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.07.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.05l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38Z"
      />
    </svg>
  );
}
