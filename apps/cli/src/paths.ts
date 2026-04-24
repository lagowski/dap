import { resolve } from "node:path";
import { homedir } from "node:os";

export const LOCAL_DAP_DIR = ".dap";
export const USER_DAP_DIR = resolve(homedir(), ".dap");

export const DEFAULT_ENGINE_PORT = 7333;
export const DEFAULT_DASHBOARD_PORT = 7332;

export function localDapPath(cwd: string = process.cwd()): string {
  return resolve(cwd, LOCAL_DAP_DIR);
}

export function localConfigPath(cwd: string = process.cwd()): string {
  return resolve(localDapPath(cwd), "config.json");
}

export function localDbPath(cwd: string = process.cwd()): string {
  return resolve(localDapPath(cwd), "state.db");
}

export function localPidPath(cwd: string = process.cwd()): string {
  return resolve(localDapPath(cwd), "dap.pid");
}
