import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import * as schema from "./schema.js";

export type Db = ReturnType<typeof drizzle<typeof schema>>;

export interface DbOptions {
  path: string;
  readonly?: boolean;
}

export function openDb(options: DbOptions): { db: Db; close: () => void } {
  const absolutePath = resolve(options.path);
  mkdirSync(dirname(absolutePath), { recursive: true });

  const sqlite = new Database(absolutePath, {
    readonly: options.readonly ?? false,
  });

  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("synchronous = NORMAL");
  sqlite.pragma("foreign_keys = ON");

  const db = drizzle(sqlite, { schema });

  return {
    db,
    close: () => sqlite.close(),
  };
}

export { schema };
