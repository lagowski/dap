import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const dashboardRoot = resolve(scriptDir, "..");
const repoRoot = resolve(dashboardRoot, "../..");
const generatedPath = join(dashboardRoot, "src/lib/api/types.gen.ts");
const check = process.argv.includes("--check");

const tempDir = mkdtempSync(join(tmpdir(), "dap-openapi-"));
const openapiPath = join(tempDir, "openapi.json");
const tempTypesPath = check ? join(tempDir, "types.gen.ts") : generatedPath;

try {
  execFileSync(
    "uv",
    ["run", "python", "-W", "ignore", "scripts/export-engine-openapi.py", "--output", openapiPath],
    {
      cwd: repoRoot,
      env: { ...process.env, PYTHONWARNINGS: "ignore" },
      stdio: "inherit",
    },
  );
  execFileSync(
    "pnpm",
    ["exec", "openapi-typescript", openapiPath, "-o", tempTypesPath],
    { cwd: dashboardRoot, stdio: "inherit" },
  );

  if (check) {
    const expected = readFileSync(tempTypesPath, "utf8");
    const current = readFileSync(generatedPath, "utf8");
    if (expected !== current) {
      console.error(
        "Generated API types are out of date. Run `pnpm --dir apps/dashboard gen:api` and commit src/lib/api/types.gen.ts.",
      );
      process.exitCode = 1;
    }
  }
} finally {
  rmSync(tempDir, { recursive: true, force: true });
}
