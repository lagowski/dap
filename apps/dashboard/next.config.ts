import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  turbopack: {
    root: process.cwd(),
  },
  // @swc/helpers ships its ESM entry points behind an `exports` map that the standalone
  // file tracer never walks: `main` is `cjs/index.cjs`, so tracing from there copies `cjs/`
  // and stops. `next/dist/server/require-hook.js` then resolves
  // `@swc/helpers/esm/_interop_require_default.js` at RUNTIME — a path no static trace saw.
  // Harmless until @swc/helpers 0.5.23 (pulled in by next 16.3.1) reorganised the package;
  // 0.5.15's traced graph happened to reach the ESM files. Told explicitly, because the
  // tracer cannot know.
  outputFileTracingIncludes: {
    "/**": ["./node_modules/.pnpm/@swc+helpers@*/node_modules/@swc/helpers/esm/**"],
  },
};

export default nextConfig;
