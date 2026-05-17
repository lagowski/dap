import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  // ``@vitejs/plugin-react`` enables automatic JSX runtime so .tsx
  // tests don't need to ``import React`` and get transformed by Babel.
  plugins: [react()],
  test: {
    // jsdom so component tests can render React via @testing-library/react.
    // Node-only tests (pure functions in lib/) still work because they
    // don't touch the DOM globals jsdom adds — one environment for the
    // whole suite keeps config simple and matches the Vitest docs.
    environment: "jsdom",
    // Include both .ts (existing lib tests) and .tsx (new component tests).
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    // Loads @testing-library/jest-dom matchers + a global afterEach
    // that calls cleanup() so DOM state doesn't leak between tests.
    setupFiles: ["./vitest.setup.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
