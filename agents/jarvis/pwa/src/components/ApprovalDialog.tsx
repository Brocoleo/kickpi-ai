/**
 * Confirmacion humana en dos pasos (Fase C).
 *
 * Pulsar "Aprobar" NO ejecuta nada: solo abre esta ventana con los datos
 * EXACTOS que ha recibido del backend (equipo, operacion, comando, riesgo,
 * caducidad). El unico acto es confirmar.
 *
 * Jerarquia visual:
 *   1. Encabezado compacto con la cuenta atras siempre visible.
 *   2. Resumen (equipo / operacion / riesgo).
 *   3. Comando exacto (monoespaciado, desplazamiento horizontal, expandible).
 *   4. Detalles tecnicos plegados.
 *   5. Acciones fijas al pie, fuera del area de scroll.
 *
 * La cuenta atras la calcula este componente en cada render con `now`
 * (el padre lo actualiza cada segundo). Al expirar, el boton de confirmar
 * se deshabilita aunque el dialogo siga abierto.
 */
import { useEffect, useRef, useState } from "react";
import type { Approval } from "../types";
import {
  canApprove,
  commandLine,
  formatCountdown,
  isExpiring,
  paramsText,
  secondsLeft,
} from "../lib/approvals";
import { ChevronIcon, ShieldCheckIcon, XIcon } from "./icons";

export type ConfirmKind = "approve" | "reject";

type Props = {
  approval: Approval;
  kind: ConfirmKind;
  busy: boolean;
  now: number;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
};

function riskTone(risk: string | null): "low" | "medium" | "high" {
  const value = (risk ?? "").toLowerCase();
  if (/alto|high|crit/.test(value)) return "high";
  if (/medio|medium|moder/.test(value)) return "medium";
  return "low";
}

export function ApprovalDialog({
  approval,
  kind,
  busy,
  now,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [techOpen, setTechOpen] = useState(false);

  const isApprove = kind === "approve";
  const left = secondsLeft(approval.expires_at, now);
  const expiring = isExpiring(approval, now);
  const expired = approval.status !== "pending" || left <= 0;
  const confirmDisabled = busy || expired || (isApprove && !canApprove(approval, now));
  const command = commandLine(approval);
  const tone = riskTone(approval.risk);

  // Bloquea el scroll de la pagina y oculta la barra de pestanas mientras
  // hay una decision pendiente: la accion no debe competir con la navegacion.
  useEffect(() => {
    document.body.classList.add("modal-open");
    return () => document.body.classList.remove("modal-open");
  }, []);

  // Al abrir: el foco entra en el dialogo (y vuelve al salir).
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    (confirmDisabled ? cancelRef.current : confirmRef.current)?.focus();
    return () => previous?.focus?.();
    // Solo al montar: no queremos robar el foco en cada tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Escape cierra (salvo que haya una accion en curso).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onCancel();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  // Mantiene el foco dentro del dialogo (no se sale con Tab).
  useEffect(() => {
    const node = panelRef.current;
    if (!node) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusables = node.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    node.addEventListener("keydown", onKey);
    return () => node.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="modal-backdrop" onClick={() => !busy && onCancel()}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-desc"
        ref={panelRef}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-head">
          <span className={`modal-mark ${isApprove ? "ok" : "danger"}`} aria-hidden="true">
            <ShieldCheckIcon />
          </span>
          <div className="modal-titles">
            <h2 id="confirm-title">
              {isApprove ? "¿Aprobar y ejecutar?" : "¿Rechazar solicitud?"}
            </h2>
            <p id="confirm-desc">
              {isApprove
                ? "Se ejecutará ahora mismo con el comando exacto de abajo."
                : "Se cerrará sin ejecutar nada."}
            </p>
          </div>
          <span
            className={`modal-timer${expiring ? " warn" : ""}`}
            role="timer"
            aria-label={`Quedan ${formatCountdown(left)}`}
          >
            <span className="modal-timer-value">{formatCountdown(left)}</span>
            <span className="modal-timer-label">{expired ? "expirada" : "restante"}</span>
          </span>
          <button
            type="button"
            className="icon-btn"
            onClick={onCancel}
            disabled={busy}
            aria-label="Cerrar sin cambios"
          >
            <XIcon />
          </button>
        </header>

        <div className="modal-body">
          <dl className="facts facts-summary">
            <div>
              <dt>Equipo</dt>
              <dd>{approval.host}</dd>
            </div>
            <div>
              <dt>Operación</dt>
              <dd className="mono">{approval.operation}</dd>
            </div>
            <div>
              <dt>Riesgo</dt>
              <dd>
                <span className={`risk ${tone}`}>{approval.risk ?? "desconocido"}</span>
              </dd>
            </div>
          </dl>

          <section className="cmd-card" aria-label="Comando exacto">
            <div className="cmd-head">
              <span className="cmd-label">Comando exacto</span>
              <button
                type="button"
                className="cmd-toggle"
                onClick={() => setExpanded((value) => !value)}
                aria-expanded={expanded}
              >
                <ChevronIcon open={expanded} />
                {expanded ? "Contraer" : "Expandir"}
              </button>
            </div>
            <pre className={`cmd-code${expanded ? " is-expanded" : ""}`} tabIndex={0}>
              {command}
            </pre>
          </section>

          <details
            className="tech"
            open={techOpen}
            onToggle={(event) => setTechOpen(event.currentTarget.open)}
          >
            <summary>
              <span>Detalles técnicos</span>
              <ChevronIcon open={techOpen} />
            </summary>
            <div className="tech-body">
              <dl className="facts facts-details">
                <div>
                  <dt>Descripción</dt>
                  <dd>{approval.risk_description ?? "Sin descripción."}</dd>
                </div>
                <div>
                  <dt>Parámetros</dt>
                  <dd className="mono">{paramsText(approval)}</dd>
                </div>
                {approval.resolved_path && (
                  <div>
                    <dt>Ruta</dt>
                    <dd className="mono">{approval.resolved_path}</dd>
                  </div>
                )}
                <div>
                  <dt>Caduca</dt>
                  <dd>{new Date(approval.expires_at).toLocaleString("es-ES")}</dd>
                </div>
              </dl>
              <p className="modal-note">
                Jarvis no puede aprobar esto por ti: la decisión es tuya y queda
                registrada con tu nombre.
              </p>
            </div>
          </details>

          {expired && (
            <p className="notice warn" role="status">
              La solicitud expiró: ya no se puede aprobar ni rechazar. Pide una nueva.
            </p>
          )}

          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </div>

        <footer className="modal-actions">
          <button
            type="button"
            className="btn"
            onClick={onCancel}
            disabled={busy}
            ref={cancelRef}
          >
            Volver
          </button>
          <button
            type="button"
            className={`btn ${isApprove ? "primary" : "danger-solid"}`}
            onClick={onConfirm}
            disabled={confirmDisabled}
            ref={confirmRef}
          >
            {busy
              ? "Trabajando…"
              : isApprove
                ? "Confirmar y ejecutar"
                : "Confirmar rechazo"}
          </button>
        </footer>
      </div>
    </div>
  );
}
