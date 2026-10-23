/**
 * Logica pura de las aprobaciones humanas (Fase C).
 *
 * Sin React ni DOM: todo lo que se puede decidir sobre una solicitud vive
 * aqui, para que sea facil de probar y de auditar de un vistazo.
 *
 * Reglas que se aplican aqui (y SOLO aqui):
 * - una solicitud se puede aprobar mientras siga `pending` y queden al
 *   menos `MIN_SECONDS_TO_APPROVE` segundos: con menos, el humain no tiene
 *   tiempo de leer lo que va a ocurrir;
 * - una solicitud `failed` significa "resultado incierto": NO se puede
 *   reintentar nunca;
 * - una solicitud `consumed` ya se ejecuto: no se repite.
 */
import type { Approval, ApprovalStatus } from "../types";

/** Segundos minimos que deben quedar para poder aprobar. */
export const MIN_SECONDS_TO_APPROVE = 10;

/** Lineas de salida mostradas antes de truncar. */
export const PREVIEW_LINES = 8;

export type Tone = "pending" | "ok" | "warn" | "danger" | "off";


export type StatusMeta = {
  label: string;
  cls: string;
  tone: Tone;
  /** Texto para lectores de pantalla. */
  aria: string;
};

const STATUS: Record<ApprovalStatus, StatusMeta> = {
  pending: {
    label: "pendiente",
    cls: "pending",
    tone: "pending",
    aria: "Solicitud pendiente de tu aprobación",
  },
  approved: {
    label: "aprobada",
    cls: "ok",
    tone: "ok",
    aria: "Solicitud aprobada, pendiente de ejecución",
  },
  executing: {
    label: "ejecutando",
    cls: "pending",
    tone: "pending",
    aria: "Solicitud en ejecución",
  },
  consumed: {
    label: "ejecutada",
    cls: "ok",
    tone: "ok",
    aria: "Solicitud ejecutada",
  },
  rejected: {
    label: "rechazada",
    cls: "off",
    tone: "off",
    aria: "Solicitud rechazada",
  },
  expired: {
    label: "expirada",
    cls: "warn",
    tone: "warn",
    aria: "Solicitud expirada",
  },
  failed: {
    label: "fallida",
    cls: "danger",
    tone: "danger",
    aria: "Solicitud fallida con resultado incierto: no se reintenta",
  },
};

export function statusMeta(status: string): StatusMeta {
  return (
    STATUS[status as ApprovalStatus] ?? {
      label: status,
      cls: "off",
      tone: "off",
      aria: `Estado ${status}`,
    }
  );
}

/** Segundos que faltan para que expire la solicitud (0 si ya expiró). */
export function secondsLeft(expiresAt: string, now: number): number {
  const target = Date.parse(expiresAt);
  if (Number.isNaN(target)) return 0;
  return Math.max(0, Math.round((target - now) / 1000));
}

/** Cuenta atrás visible: "4:07". */
export function formatCountdown(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** ¿La solicitud se puede aprobar ahora mismo? */
export function canApprove(approval: Approval, now: number): boolean {
  return (
    approval.status === "pending" &&
    secondsLeft(approval.expires_at, now) >= MIN_SECONDS_TO_APPROVE
  );
}

/** ¿Quedan pocos segundos para que expire? (aviso visual) */
export function isExpiring(approval: Approval, now: number): boolean {
  if (approval.status !== "pending") return false;
  const left = secondsLeft(approval.expires_at, now);
  return left < MIN_SECONDS_TO_APPROVE;
}

/** ¿El resultado de la ejecución es incierto? Entonces NO se reintenta. */
export function isUncertain(approval: Approval): boolean {
  return approval.status === "failed";
}

/** ¿La solicitud ya se consumió (ejecutada)? */
export function isConsumed(approval: Approval): boolean {
  return approval.status === "consumed";
}

/** Estados que siguen vivos: aparecen en "Pendientes". */
export function isOpen(approval: Approval): boolean {
  return (
    approval.status === "pending" ||
    approval.status === "approved" ||
    approval.status === "executing"
  );
}

/** El argv exacto, listo para mostrar. */
export function commandLine(approval: Approval): string {
  return (approval.argv ?? []).join(" ");
}

/** Parametros en texto legible (los que recibio el backend, sin inventar). */
export function paramsText(approval: Approval): string {
  const params = approval.params ?? {};
  const keys = Object.keys(params);
  if (keys.length === 0) return "sin parámetros";
  return keys.map((key) => `${key}=${String(params[key])}`).join("  ");
}

/** Datos exactos que se muestran en la confirmación. */
export function confirmationFacts(approval: Approval, now: number): {
  label: string;
  value: string;
}[] {
  const facts: { label: string; value: string }[] = [
    { label: "Equipo", value: approval.host },
    { label: "Operación", value: approval.operation },
  ];
  if (approval.project_id) {
    facts.push({ label: "Proyecto", value: approval.project_id });
  }
  facts.push({ label: "Riesgo", value: approval.risk ?? "desconocido" });
  if (approval.risk_description) {
    facts.push({ label: "Descripción", value: approval.risk_description });
  }
  facts.push({ label: "Comando exacto", value: commandLine(approval) });
  facts.push({ label: "Parámetros", value: paramsText(approval) });
  if (approval.resolved_path) {
    facts.push({ label: "Ruta resuelta", value: approval.resolved_path });
  }
  facts.push({
    label: "Caduca en",
    value: formatCountdown(secondsLeft(approval.expires_at, now)),
  });
  return facts;
}

/** Recorta una salida larga y avisa de cuanto falta. */
export function truncateOutput(
  output: string,
  maxLines = PREVIEW_LINES,
): { text: string; truncated: boolean; hiddenLines: number } {
  const lines = (output ?? "").split("\n");
  if (lines.length <= maxLines) {
    return { text: output ?? "", truncated: false, hiddenLines: 0 };
  }
  const shown = lines.slice(0, maxLines);
  return {
    text: shown.join("\n"),
    truncated: true,
    hiddenLines: lines.length - maxLines,
  };
}

/** Duracion legible: "1.2 s" / "340 ms". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
