import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  turbopack: {
    root: process.cwd(),
  },
  env: {
    NEXT_PUBLIC_DAP_ENGINE_URL:
      process.env.NEXT_PUBLIC_DAP_ENGINE_URL ?? "http://127.0.0.1:7333",
  },
};

export default nextConfig;
