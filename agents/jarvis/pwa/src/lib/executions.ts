import type { Ejecucion } from "../types";

/** Hace cuánto, en texto corto, a partir de una fecha ISO. */
export function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `hace ${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  return `hace ${Math.round(hours / 24)} d`;
}

/**
 * Estado de una ejecución del ejecutor.
 *
 * `allowed` se guarda como entero en SQLite (0/1), no como booleano: la
 * comprobación es explícita para no confundir "denegada" con "falló".
 */
export function executionState(item: Ejecucion): { label: string; cls: string } {
  if (!item.allowed) return { label: "denegado", cls: "warn" };
  if (item.ok) return { label: "ok", cls: "ok" };
  return { label: `error ${item.exit_code ?? "?"}`, cls: "warn" };
}
