/**
 * Centred-card layout for the auth pages (``/login``, ``/signup``,
 * ``/forgot-password``, ``/reset-password``). No sidebar — those
 * pages are reachable from the public middleware allowlist and
 * showing the app chrome would imply the user is signed in.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-muted/30 px-4 py-12">
      <div className="w-full max-w-md">{children}</div>
    </div>
  );
}
