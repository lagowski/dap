import { existsSync } from "node:fs";
import kleur from "kleur";
import { createEngine } from "@dap/engine";
import { localDapPath, localDbPath, DEFAULT_DASHBOARD_PORT, DEFAULT_ENGINE_PORT } from "../paths.js";

export interface StartOptions {
  port?: string;
  enginePort?: string;
  headless?: boolean;
}

export async function startCommand(options: StartOptions): Promise<void> {
  if (!existsSync(localDapPath())) {
    console.log(kleur.red("✗ No .dap/ found in current directory."));
    console.log(kleur.dim("  Run `dap init` first."));
    process.exit(1);
  }

  const enginePort = Number(options.enginePort ?? DEFAULT_ENGINE_PORT);
  const dashboardPort = Number(options.port ?? DEFAULT_DASHBOARD_PORT);

  console.log(kleur.cyan("Starting DAP..."));

  const engine = await createEngine({
    dbPath: localDbPath(),
    host: "127.0.0.1",
    port: enginePort,
  });

  const engineAddress = await engine.start();
  console.log(kleur.green(`✓ engine  ${engineAddress}`));
  console.log(kleur.yellow(`○ dashboard  http://127.0.0.1:${dashboardPort}  (not yet implemented — F6)`));

  if (!options.headless) {
    console.log(kleur.dim("  (would open browser in --no-headless mode after F6)"));
  }

  console.log();
  console.log(kleur.dim("Press Ctrl+C to stop."));

  const shutdown = async (): Promise<void> => {
    console.log();
    console.log(kleur.dim("Stopping..."));
    await engine.stop();
    process.exit(0);
  };

  process.on("SIGINT", () => void shutdown());
  process.on("SIGTERM", () => void shutdown());
}
