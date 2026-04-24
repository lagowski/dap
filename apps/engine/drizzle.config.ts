import type { Config } from "drizzle-kit";

export default {
  schema: "./src/persistence/schema.ts",
  out: "./drizzle",
  dialect: "sqlite",
  dbCredentials: {
    url: process.env["DAP_DB_PATH"] ?? "./.dap/state.db",
  },
} satisfies Config;
