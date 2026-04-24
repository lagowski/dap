import { existsSync } from "node:fs";
import kleur from "kleur";
import { localDapPath } from "../paths.js";

export function statusCommand(): void {
  const dapDir = localDapPath();

  if (!existsSync(dapDir)) {
    console.log(kleur.red("✗ Not a DAP project"));
    console.log(kleur.dim("  Run `dap init` to initialize."));
    return;
  }

  console.log(kleur.green("✓ DAP project"));
  console.log(kleur.dim(`  ${dapDir}`));
  console.log();
  console.log(kleur.yellow("⚠ runtime status check — to be implemented in F1"));
}
