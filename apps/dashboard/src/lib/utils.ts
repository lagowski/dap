import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}

export function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "—";
  if (cost < 0.01) return `<$0.01`;
  return `$${cost.toFixed(2)}`;
}

export function formatTokens(tokens: number | null | undefined): string {
  if (tokens == null) return "—";
  if (tokens < 1_000) return tokens.toString();
  if (tokens < 1_000_000) return `${(tokens / 1_000).toFixed(1)}k`;
  return `${(tokens / 1_000_000).toFixed(2)}M`;
}
