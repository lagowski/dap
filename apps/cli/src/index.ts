#!/usr/bin/env node
import { Command } from "commander";
import { initCommand } from "./commands/init.js";
import { startCommand } from "./commands/start.js";
import { stopCommand } from "./commands/stop.js";
import { statusCommand } from "./commands/status.js";

const program = new Command();

program
  .name("dap")
  .description("Deterministic Agent Pipeline — local launcher")
  .version("0.0.1", "-v, --version");

program
  .command("init")
  .description("Initialize DAP project in current directory")
  .option("-f, --force", "overwrite existing .dap/")
  .action((options: { force?: boolean }) => initCommand(options));

program
  .command("start")
  .description("Start engine + dashboard, open browser")
  .option("-p, --port <port>", "dashboard port", "7332")
  .option("--engine-port <port>", "engine port", "7333")
  .option("--headless", "do not open browser")
  .action((options) => startCommand(options));

program
  .command("stop")
  .description("Stop running engine + dashboard")
  .action(() => stopCommand());

program
  .command("status")
  .description("Show DAP project status")
  .action(() => statusCommand());

program.parseAsync(process.argv).catch((err: unknown) => {
  console.error(err);
  process.exit(1);
});
