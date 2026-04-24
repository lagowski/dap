import { mkdirSync, writeFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import kleur from "kleur";
import { localDapPath, localConfigPath } from "../paths.js";

export function initCommand(options: { force?: boolean }): void {
  const dapDir = localDapPath();
  const configPath = localConfigPath();

  if (existsSync(dapDir) && !options.force) {
    console.log(kleur.yellow(`⚠ .dap/ already exists at ${dapDir}`));
    console.log(kleur.dim("  Use --force to reinitialize."));
    return;
  }

  mkdirSync(resolve(dapDir, "pipelines"), { recursive: true });
  mkdirSync(resolve(dapDir, "agents"), { recursive: true });
  mkdirSync(resolve(dapDir, "runs"), { recursive: true });

  const defaultConfig = {
    version: 1,
    engine: {
      host: "127.0.0.1",
      port: 7333,
    },
    dashboard: {
      port: 7332,
    },
    runtimes: {
      paths: {},
    },
  };

  writeFileSync(configPath, JSON.stringify(defaultConfig, null, 2) + "\n", "utf8");

  console.log(kleur.green("✓ Initialized DAP project"));
  console.log(kleur.dim(`  ${dapDir}/`));
  console.log(kleur.dim(`  ├─ config.json`));
  console.log(kleur.dim(`  ├─ pipelines/`));
  console.log(kleur.dim(`  ├─ agents/`));
  console.log(kleur.dim(`  └─ runs/`));
  console.log();
  console.log(kleur.cyan("Next: dap start"));
}
