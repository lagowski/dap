import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // ESLint disabled during build for F6 — TS check happens via `pnpm typecheck`.
  // ESLint setup is a follow-up issue.
  eslint: { ignoreDuringBuilds: true },
  env: {
    NEXT_PUBLIC_DAP_ENGINE_URL:
      process.env.NEXT_PUBLIC_DAP_ENGINE_URL ?? "http://127.0.0.1:7333",
  },
};

export default nextConfig;
